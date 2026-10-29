package org.anastylos.capture.camera

import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
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
import org.anastylos.capture.guidance.PoseEstimator
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
    private var analysisExecutor: java.util.concurrent.ExecutorService? = null
    private var board: PoseEstimator.BoardSpec? = null
    private var intrinsics: PoseEstimator.Intrinsics? = null
    private var lastEstimateNs: Long? = null
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
        val back = cameraProvider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)
        val front = cameraProvider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)
        if (!back && !front) return false
        lensFacing = when {
            lensFacing == CameraSelector.LENS_FACING_BACK && back -> lensFacing
            back -> CameraSelector.LENS_FACING_BACK
            else -> CameraSelector.LENS_FACING_FRONT
        }
        val selector = CameraSelector.Builder().requireLensFacing(lensFacing).build()
        // Preview and analysis request the same resolution so the
        // detection overlay can map analysis pixels onto the FIT_CENTER
        // preview analytically (no restricted transform APIs).
        val resolutionSelector =
            androidx.camera.core.resolutionselector.ResolutionSelector.Builder()
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
        val capture = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
            .setJpegQuality(95)
            .setFlashMode(ImageCapture.FLASH_MODE_OFF)
            .build()
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
            imageCapture = capture
            boundPreview = preview
            boundAnalysis = analysis
            intrinsics = Camera2Intrinsics.forAnalysisStream(camera, analysisWidth, analysisHeight)
            geometry = readGeometry(preview, analysis, analysisWidth, analysisHeight)
            true
        } catch (_: Exception) {
            imageCapture = null
            boundPreview = null
            boundAnalysis = null
            intrinsics = null
            geometry = null
            false
        }
    }

    override fun streamGeometry(): GuidedCamera.StreamGeometry? = geometry

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
            )
        } catch (_: Exception) {
            null
        }

    private fun analyzeFrame(image: ImageProxy) {
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
            val src = ByteArray(plane.buffer.remaining())
            plane.buffer.get(src)
            val tight = try {
                YuvFrame.copyYPlane(src, width, height, rowStride)
            } catch (_: IllegalArgumentException) {
                return
            }
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
            _estimates.tryEmit(estimate)
        } finally {
            try {
                image.close()
            } catch (_: Exception) {
            }
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
