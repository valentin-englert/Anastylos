package org.anastylos.capture.camera

import android.util.Size
import android.util.Log
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File
import java.util.concurrent.Executors
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.withContext
import org.anastylos.capture.guidance.DIAG_LOG_TAG
import org.anastylos.capture.guidance.PoseEstimator
import org.anastylos.capture.guidance.TimingAccumulator
import org.anastylos.capture.guidance.YuvFrame
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/**
 * Guided-capture camera source: one coherent [Preview] +
 * [ImageAnalysis] + [ImageCapture] binding owned by the guided
 * screen. The legacy [CameraStillSource] keeps serving the session
 * screen; the two are never bound at once (each `bindAsync` starts
 * from `unbindAll`, taking the camera over from the previous
 * screen). Dispose unbinds only the owned use cases: on forward
 * navigation the entering screen binds before the leaving screen
 * disposes, so a dispose-time `unbindAll` would kill the new
 * owner's camera (black preview, no error).
 *
 * Analyzer contract: YUV_420_888, KEEP_ONLY_LATEST, single bounded
 * worker; every ImageProxy is closed in `finally`, analyzer
 * exceptions are contained (the frame drops, the preview lives),
 * and estimates flow through a conflated latest-only buffer. The
 * estimator never blocks the UI thread and never sees an ImageProxy.
 */
class GuidedCameraSource(
    private val context: android.content.Context,
    private val lifecycleOwner: LifecycleOwner,
    private val providerSource: CameraProviderSource,
    private val estimator: PoseEstimator,
    private val nowUtc: () -> java.time.Instant,
    private val inventory: CameraInventorySource = CameraXInventorySource(),
    private val settingsApplier: SettingsApplier = CameraXSettingsApplier(),
    private val measuredProfiles: MeasuredProfileSource = AssetMeasuredProfileSource(context),
    private val timing: TimingAccumulator = TimingAccumulator(),
) : GuidedCamera {
    data class GuidedStill(
        val bytes: ByteArray,
        val capturedAt: java.time.Instant,
        /** Nearest analyzer frame timestamp (monotonic ns), if any. */
        val nearestEstimateMonotonicNs: Long?,
        /** Honest association note; never a fabricated exposure link. */
        val associationNote: String,
    )

    private var provider: ProcessCameraProvider? = null
    private var imageCapture: ImageCapture? = null
    private var boundPreview: Preview? = null
    private var boundAnalysis: ImageAnalysis? = null
    private var previewView: PreviewView? = null
    private var lensFacing: Int = CameraSelector.LENS_FACING_BACK

    /** Operator-requested Camera2 ID (null = safe default). Set before [bindAsync]. */
    override var requestedCameraId: String? = null

    /** Effective Camera2 ID of the bound camera, null before a bind. */
    override var effectiveCameraId: String? = null
        private set

    /** Substitution note when the request fell back, else null. */
    override var cameraNote: String? = null
        private set

    private var lastInventory: List<UsableCamera> = emptyList()

    /** Live inventory from the last bind (empty before first bind). */
    override fun availableCameras(): List<UsableCamera> = lastInventory

    override var requestedStillSize: Pair<Int, Int>? = null

    // CAMERA-02 API verdict: no public CameraX 1.6.2 path to
    // per-frame CaptureResult exists (CameraCaptureResultImageInfo
    // and ImageInfo.getTagBundle are both same-library
    // RestrictedApi), so this stays null and the UI reports
    // requested settings plus "Applied: n/a".
    @Volatile
    override var lastApplied: AppliedSettings? = null
        private set

    private var boundCamera: Camera? = null
    private var lastAnalysisSize: Pair<Int, Int> = 1280 to 720
    private var currentZoom: Float = 0f
    private var boundSelector: CameraSelector? = null
    private var lastStillSettings: CameraSettings = CameraSettings()
    private var lastStillCaps: CameraCapabilities = CameraCapabilities()
    private var lastStillOptions: StillOptions? = null

    @ExperimentalCamera2Interop
    override fun applySettings(settings: CameraSettings, caps: CameraCapabilities): Boolean {
        val prevFocus = lastStillSettings.focusMode to lastStillSettings.focusDiopters
        lastStillSettings = settings
        lastStillCaps = caps
        val cam = boundCamera ?: return false
        val ok = settingsApplier.apply(cam, settings, caps)
        // The measured profile is bound to zoom AND focus: either change
        // re-keys (a new profile, never a silent reuse).
        val focusChanged = settings.focusMode != prevFocus.first ||
            settings.focusDiopters != prevFocus.second
        if (ok && (settings.linearZoom != currentZoom || focusChanged)) {
            currentZoom = settings.linearZoom
            refreshIntrinsics()
        }
        return ok
    }

    /** Re-keys intrinsics after a zoom/focus change (new profile, no silent reuse). */
    @ExperimentalCamera2Interop
    private fun refreshIntrinsics() {
        val cam = boundCamera ?: return
        intrinsics = resolveIntrinsics(cam, lastAnalysisSize.first, lastAnalysisSize.second)
        Log.i(CAMERA_LOG_TAG, "guided intrinsics refreshed profile=${intrinsics?.profileId}")
    }

    /**
     * Measured profile first, factory fallback: the stored CAL-01
     * profile wins only when the live camera, analysis size, zoom,
     * and manual focus all match its recording configuration;
     * otherwise the uncalibrated factory intrinsics keep detection
     * running without ever validating a metric pose.
     */
    @ExperimentalCamera2Interop
    private fun resolveIntrinsics(
        cam: Camera,
        analysisWidth: Int,
        analysisHeight: Int,
    ): PoseEstimator.Intrinsics? {
        val measured = try {
            measuredProfiles.forGeometry(
                effectiveCameraId, analysisWidth, analysisHeight, currentZoom,
                lastStillSettings.focusMode, lastStillSettings.focusDiopters,
            )
        } catch (_: Exception) {
            null
        }
        if (measured != null) {
            Log.i(CAMERA_LOG_TAG, "guided intrinsics measured profile=${measured.profileId}")
            return measured
        }
        Log.i(
            CAMERA_LOG_TAG,
            "guided intrinsics factory fallback " +
                "camera=$effectiveCameraId analysis=${analysisWidth}x$analysisHeight " +
                "zoom=$currentZoom focus=${lastStillSettings.focusMode}@${lastStillSettings.focusDiopters}",
        )
        return try {
            Camera2Intrinsics.forAnalysisStream(
                cam, analysisWidth, analysisHeight, currentZoom,
            )
        } catch (_: Exception) {
            null
        }
    }
    private var analysisExecutor: java.util.concurrent.ExecutorService? = null
    private var board: PoseEstimator.BoardSpec? = null
    private var intrinsics: PoseEstimator.Intrinsics? = null
    private var lastEstimateNs: Long? = null
    private var lastFrameArrivedNs: Long? = null
    private var lastFrameSensorTs: Long? = null
    @Volatile private var geometry: GuidedCamera.StreamGeometry? = null
    @Volatile private var lastAnalysisRotation: Int = 0

    private val _estimates = MutableSharedFlow<PoseEstimator.Estimate>(
        replay = 0,
        extraBufferCapacity = 1,
    )
    /** Latest-only estimate stream (conflated; slow collectors drop). */
    override val estimates: SharedFlow<PoseEstimator.Estimate> = _estimates.asSharedFlow()

    /** Intrinsics resolved for the bound stream, or null (detection-only). */
    override fun currentIntrinsics(): PoseEstimator.Intrinsics? = intrinsics

    override fun attachPreview(view: PreviewView?) {
        previewView = view
    }

    override fun setBoard(board: PoseEstimator.BoardSpec?) {
        this.board = board
    }

    /**
     * Binds preview + analysis + capture. Returns false (no throw)
     * when the provider, a camera, or the bind itself is unavailable.
     */
    @androidx.camera.camera2.interop.ExperimentalCamera2Interop
    override suspend fun bindAsync(analysisWidth: Int, analysisHeight: Int): Boolean {
        val cameraProvider = acquireProviderOrNull(providerSource) ?: return false
        return withContext(Dispatchers.Main.immediate) { bindProvider(cameraProvider, analysisWidth, analysisHeight) }
    }

    @androidx.camera.camera2.interop.ExperimentalCamera2Interop
    private fun bindProvider(
        cameraProvider: ProcessCameraProvider,
        analysisWidth: Int,
        analysisHeight: Int,
    ): Boolean {
        provider = cameraProvider
        val cameras = try {
            inventory.list(cameraProvider)
        } catch (_: Exception) {
            emptyList()
        }
        lastInventory = cameras
        val resolution = resolveCamera(requestedCameraId, cameras)
        if (resolution == null) {
            effectiveCameraId = null
            cameraNote = "no usable camera found"
            return false
        }
        val effective = resolution.camera
        lensFacing = effective.lensFacing
        effectiveCameraId = effective.id
        cameraNote = resolution.note
        Log.i(
            CAMERA_LOG_TAG,
            "guided bind requested=${requestedCameraId ?: "<default>"} " +
                "effective=${effective.id} (${effective.label}) " +
                "available=[${cameras.joinToString { it.id }}]" +
                (resolution.note?.let { " note=$it" } ?: ""),
        )
        val selector = CameraSelector.Builder()
            .addCameraFilter { infos ->
                infos.filter {
                    try {
                        Camera2CameraInfo.from(it).cameraId == effective.id
                    } catch (_: Exception) {
                        false
                    }
                }
            }
            .build()
        boundSelector = selector
        // Preview and analysis request the same resolution so the
        // detection overlay can map analysis pixels onto the FIT_CENTER
        // preview analytically (no restricted transform APIs).
        val resolutionSelector =
            androidx.camera.core.resolutionselector.ResolutionSelector.Builder()
                .setAspectRatioStrategy(aspectStrategyFor(analysisWidth, analysisHeight))
                .setResolutionStrategy(
                    androidx.camera.core.resolutionselector.ResolutionStrategy(
                        android.util.Size(analysisWidth, analysisHeight),
                        androidx.camera.core.resolutionselector.ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER,
                    ),
                )
                .build()
        val preview = Preview.Builder().setResolutionSelector(resolutionSelector).build()
        previewView?.let { preview.setSurfaceProvider(it.surfaceProvider) }
        val analysis = ImageAnalysis.Builder()
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .setResolutionSelector(resolutionSelector)
            .build()
        val capture = buildCapture(
            stillOptionsFor(lastStillSettings, lastStillCaps),
        )
        previewView?.display?.rotation?.let {
            preview.targetRotation = it
            analysis.targetRotation = it
            capture.targetRotation = it
        }
        val executor = Executors.newSingleThreadExecutor()
        analysisExecutor?.shutdownNow()
        analysisExecutor = executor
        analysis.setAnalyzer(executor) { image -> analyzeFrame(image) }
        return try {
            cameraProvider.unbindAll()
            val camera = cameraProvider.bindToLifecycle(lifecycleOwner, selector, preview, analysis, capture)
            boundCamera = camera
            imageCapture = capture
            boundPreview = preview
            boundAnalysis = analysis
            lastAnalysisSize = analysisWidth to analysisHeight
            intrinsics = resolveIntrinsics(camera, analysisWidth, analysisHeight)
            geometry = readGeometry(preview, analysis, analysisWidth, analysisHeight)
            val geo = geometry
            Log.i(
                CAMERA_LOG_TAG,
                "guided stream camera=${effectiveCameraId} " +
                    "analysis=${geo?.analysisWidth}x${geo?.analysisHeight} " +
                    "preview=${geo?.previewWidth}x${geo?.previewHeight} " +
                    "profile=${intrinsics?.profileId}",
            )
            true
        } catch (e: Exception) {
            cameraNote = "bind failed for camera " + (effectiveCameraId ?: "?")
            Log.w(CAMERA_LOG_TAG, "guided bind failed for camera $effectiveCameraId", e)
            boundCamera = null
            imageCapture = null
            boundPreview = null
            boundAnalysis = null
            intrinsics = null
            geometry = null
            false
        }
    }

    override fun streamGeometry(): GuidedCamera.StreamGeometry? = geometry

    /** GUIDE-01 diagnostics: throttled per-frame cost + quality summary, else null. */
    private fun recordFrameTiming(
        arrivedNs: Long,
        gapMs: Double,
        copyStartNs: Long,
        copyEndNs: Long,
        width: Int,
        height: Int,
        sensorTs: Long,
        estimate: PoseEstimator.Estimate,
    ) {
        val nowNs = System.nanoTime()
        val tsGapMs = lastFrameSensorTs?.let { (sensorTs - it) / 1_000_000.0 } ?: 0.0
        lastFrameSensorTs = sensorTs
        val summary = timing.recordFrame(
            copyMs = (copyEndNs - copyStartNs) / 1_000_000.0,
            totalMs = (nowNs - arrivedNs) / 1_000_000.0,
            gapMs = gapMs,
            tsGapMs = tsGapMs,
            width = width,
            height = height,
            darkFraction = estimate.frameQuality?.darkFraction,
            saturatedFraction = estimate.frameQuality?.saturatedFraction,
            sharpness = estimate.frameQuality?.sharpness,
        )
        if (summary != null) Log.i(DIAG_LOG_TAG, "guided $summary")
    }

    private fun readGeometry(
        preview: Preview,
        analysis: ImageAnalysis,
        requestedWidth: Int,
        requestedHeight: Int,
    ): GuidedCamera.StreamGeometry? =
        try {
            val resolvedAnalysis = analysis.resolutionInfo?.resolution
            val resolvedPreview = preview.resolutionInfo?.resolution
            GuidedCamera.StreamGeometry(
                analysisWidth = resolvedAnalysis?.width ?: requestedWidth,
                analysisHeight = resolvedAnalysis?.height ?: requestedHeight,
                analysisRotationDeg = lastAnalysisRotation,
                previewWidth = resolvedPreview?.width ?: requestedWidth,
                previewHeight = resolvedPreview?.height ?: requestedHeight,
                lensFacing = lensFacing,
                cameraId = effectiveCameraId ?: "",
            )
        } catch (_: Exception) {
            null
        }

    @androidx.camera.camera2.interop.ExperimentalCamera2Interop
    private fun analyzeFrame(image: ImageProxy) {
        val arrivedNs = System.nanoTime()
        val gapMs = lastFrameArrivedNs?.let { (arrivedNs - it) / 1_000_000.0 } ?: 0.0
        lastFrameArrivedNs = arrivedNs
        try {
            val frameRotation = try {
                image.imageInfo.rotationDegrees
            } catch (_: Exception) {
                0
            }
            if (frameRotation != lastAnalysisRotation) {
                lastAnalysisRotation = frameRotation
                geometry = geometry?.copy(analysisRotationDeg = frameRotation)
            }
            if (image.format != android.graphics.ImageFormat.YUV_420_888) return
            val plane = image.planes.getOrNull(0) ?: return
            if (plane.pixelStride != 1) return
            val width = image.width
            val height = image.height
            val rowStride = plane.rowStride
            val copyStartNs = System.nanoTime()
            val src = ByteArray(plane.buffer.remaining())
            plane.buffer.get(src)
            val tight = try {
                YuvFrame.copyYPlane(src, width, height, rowStride)
            } catch (_: IllegalArgumentException) {
                return
            }
            val copyEndNs = System.nanoTime()
            val active = board ?: return
            val timestampNs = image.imageInfo.timestamp
            val input = PoseEstimator.Input(
                yPlane = tight,
                width = width,
                height = height,
                rotationDegrees = image.imageInfo.rotationDegrees,
                sensorTimestampNs = timestampNs,
                monotonicNs = timestampNs,
                // Stream-geometry epoch: any rotation/crop rebind changes
                // the analyzer dimensions and starts a new epoch, which
                // the session reducer treats as a disarm event.
                transformEpochId = "analysis-${width}x${height}",
            )
            val estimate = try {
                estimator.estimate(input, intrinsics, active)
            } catch (_: Exception) {
                return
            }
            lastEstimateNs = estimate.monotonicNs
            recordFrameTiming(arrivedNs, gapMs, copyStartNs, copyEndNs, width, height, timestampNs, estimate)
            _estimates.tryEmit(estimate)
        } finally {
            try {
                image.close()
            } catch (_: Exception) {
            }
        }
    }

    @ExperimentalCamera2Interop
    private fun buildCapture(opts: StillOptions): ImageCapture {
        val captureBuilder = ImageCapture.Builder()
            .setCaptureMode(opts.captureMode)
            .setJpegQuality(95)
            .setFlashMode(ImageCapture.FLASH_MODE_OFF)
        applyStillOptions(captureBuilder, opts)
        requestedStillSize?.let { (w, h) ->
            captureBuilder.setResolutionSelector(
                ResolutionSelector.Builder()
                    .setResolutionStrategy(
                        ResolutionStrategy(
                            Size(w, h),
                            ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER,
                        ),
                    )
                    .build(),
            )
        }
        return captureBuilder.build()
    }

    /**
     * Bakes the latest settings into ImageCapture and
     * selectively rebinds that use case alone; Preview and
     * analysis stay attached. Skips the rebind when the still
     * slice is unchanged. On failure the previous ImageCapture
     * is restored and false is returned.
     */
    @ExperimentalCamera2Interop
    override suspend fun refreshStillOptions(): Boolean {
        val opts = stillOptionsFor(lastStillSettings, lastStillCaps)
        if (opts == lastStillOptions && imageCapture != null) return true
        return withContext(Dispatchers.Main.immediate) { rebindCapture(opts) }
    }

    @ExperimentalCamera2Interop
    private fun rebindCapture(opts: StillOptions): Boolean {
        val cameraProvider = provider ?: return false
        val old = imageCapture ?: return false
        val selector = boundSelector ?: return false
        return try {
            val capture = buildCapture(opts)
            capture.targetRotation = old.targetRotation
            cameraProvider.unbind(old)
            boundCamera = cameraProvider.bindToLifecycle(lifecycleOwner, selector, capture)
            imageCapture = capture
            lastStillOptions = opts
            val cam = boundCamera
            if (cam != null) {
                settingsApplier.apply(cam, lastStillSettings, lastStillCaps)
            }
            Log.i(
                CAMERA_LOG_TAG,
                "guided still opts refreshed mode=${opts.captureMode} ae=${opts.aeMode} " +
                    "iso=${opts.iso} exp=${opts.exposureNs} af=${opts.afMode} " +
                    "awb=${opts.awbMode} ev=${opts.evSteps}",
            )
            true
        } catch (e: Exception) {
            Log.w(CAMERA_LOG_TAG, "guided still options rebind failed, restoring previous", e)
            try {
                boundCamera = cameraProvider.bindToLifecycle(lifecycleOwner, selector, old)
                imageCapture = old
            } catch (_: Exception) {
                boundCamera = null
                imageCapture = null
            }
            lastStillOptions = null
            false
        }
    }


    override fun unbind() {
        try {
            val owned = listOfNotNull(boundPreview, boundAnalysis, imageCapture)
            if (owned.isNotEmpty()) {
                provider?.unbind(*owned.toTypedArray())
            }
        } catch (_: Exception) {
        }
        try {
            analysisExecutor?.shutdownNow()
        } catch (_: Exception) {
        }
        analysisExecutor = null
        imageCapture = null
        boundPreview = null
        boundAnalysis = null
        intrinsics = null
        boundCamera = null
        geometry = null
        lastAnalysisRotation = 0
    }

    /**
     * One full-resolution JPEG still. Throws on any camera failure.
     * Exposure-time association is reported honestly: the nearest
     * analyzer timestamp is informational only, and the sidecar
     * stays explicitly unqualified until a real exposure link exists.
     */
    override suspend fun takeGuidedStill(): GuidedStill {
        val capture = imageCapture ?: throw ImageCaptureException(
            ImageCapture.ERROR_UNKNOWN,
            "guided camera is not bound",
            null,
        )
        val tmp = File.createTempFile("guided-", ".jpg", context.cacheDir)
        try {
            suspendCoroutine { cont ->
                val options = ImageCapture.OutputFileOptions.Builder(tmp).build()
                capture.takePicture(
                    options,
                    ContextCompat.getMainExecutor(context),
                    object : ImageCapture.OnImageSavedCallback {
                        override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                            cont.resumeWith(Result.success(Unit))
                        }

                        override fun onError(exception: ImageCaptureException) {
                            cont.resumeWithException(exception)
                        }
                    },
                )
            }
            val nearest = lastEstimateNs
            return GuidedStill(
                bytes = tmp.readBytes(),
                capturedAt = nowUtc(),
                nearestEstimateMonotonicNs = nearest,
                associationNote = if (nearest == null) {
                    "no analyzer frame near shutter; spatial sidecar unqualified"
                } else {
                    "nearest analyzer frame is informational only; spatial sidecar unqualified"
                },
            )
        } finally {
            tmp.delete()
        }
    }
}
