package org.anastylos.capture.camera

import android.content.Context
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.anastylos.capture.domain.nowUtc

/** Default [CameraProviderSource]: the real CameraX provider future. */
class DefaultCameraProviderSource(
    private val context: Context,
) : CameraProviderSource {
    override fun providerFuture() = ProcessCameraProvider.getInstance(context)
}

/**
 * CameraX still source for the frame sampler. Device-only: binds
 * Preview + ImageCapture to the caller's lifecycle and hands full
 * JPEG stills back as bytes.
 *
 * Binding is main-safe: [bindAsync] suspends on provider completion
 * and never blocks a thread, then performs the CameraX-required bind
 * on the main thread. Cancelling the caller cancels a pending
 * provider wait; a stalled provider surfaces as bind failure, never
 * an ANR.
 *
 * Capability honesty: every control the UI exposes ([hasBackCamera],
 * [hasFrontCamera], [hasFlashUnit]) is probed on the real device at
 * bind time. Nothing is assumed.
 */
class CameraStillSource(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
    private val previewView: PreviewView? = null,
    private val providerSource: CameraProviderSource = DefaultCameraProviderSource(context),
) {
    private var provider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private var imageCapture: ImageCapture? = null
    private var boundPreview: Preview? = null

    var lensFacing: Int = CameraSelector.LENS_FACING_BACK
        private set

    var flashEnabled: Boolean = false

    val isBound: Boolean get() = camera != null

    fun hasBackCamera(): Boolean =
        provider?.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA) ?: false

    fun hasFrontCamera(): Boolean =
        provider?.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA) ?: false

    fun hasFlashUnit(): Boolean = try {
        camera?.cameraInfo?.hasFlashUnit() == true
    } catch (_: Exception) {
        false
    }

    /**
     * Binds the selected lens without blocking any thread. Falls back
     * to whichever lens exists when the requested one is absent;
     * returns false when the device has no camera at all or the
     * provider fails. Cancellation propagates to a pending provider
     * wait; the CameraX bind itself runs on the main thread.
     */
    suspend fun bindAsync(): Boolean {
        val cameraProvider = acquireProviderOrNull(providerSource) ?: return false
        return withContext(Dispatchers.Main.immediate) { bindProvider(cameraProvider) }
    }

    private fun bindProvider(cameraProvider: ProcessCameraProvider): Boolean {
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
        val preview = Preview.Builder().build()
        previewView?.let { preview.setSurfaceProvider(it.surfaceProvider) }
        val capture = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
            .setJpegQuality(95)
            .setFlashMode(
                if (flashEnabled) ImageCapture.FLASH_MODE_ON else ImageCapture.FLASH_MODE_OFF,
            )
            .build()
        previewView?.display?.rotation?.let { capture.targetRotation = it }
        cameraProvider.unbindAll()
        camera = cameraProvider.bindToLifecycle(lifecycleOwner, selector, preview, capture)
        imageCapture = capture
        boundPreview = preview
        return true
    }

    /**
     * Releases only this source's use cases. A dispose-time
     * `unbindAll` would detach the next screen's camera: on forward
     * navigation the entering screen binds before the leaving
     * screen disposes (black preview, no error).
     */
    fun unbind() {
        try {
            val owned = listOfNotNull(boundPreview, imageCapture)
            if (owned.isNotEmpty()) {
                provider?.unbind(*owned.toTypedArray())
            }
        } catch (_: Exception) {
        }
        camera = null
        imageCapture = null
        boundPreview = null
    }

    fun setFlash(enabled: Boolean) {
        flashEnabled = enabled
        imageCapture?.flashMode =
            if (enabled) ImageCapture.FLASH_MODE_ON else ImageCapture.FLASH_MODE_OFF
    }

    /** One full-resolution JPEG still. Throws on any camera failure. */
    suspend fun takeStill(): CapturedStill {
        val capture = imageCapture ?: throw ImageCaptureException(
            ImageCapture.ERROR_UNKNOWN,
            "camera is not bound",
            null,
        )
        val tmp = File.createTempFile("frame-", ".jpg", context.cacheDir)
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
                            cont.resumeWith(Result.failure(exception))
                        }
                    },
                )
            }
            return CapturedStill(bytes = tmp.readBytes(), capturedAt = nowUtc())
        } finally {
            tmp.delete()
        }
    }

    companion object {
        /** False on misconfigured devices where CameraX cannot initialize. */
        suspend fun isAvailable(source: CameraProviderSource): Boolean =
            acquireProviderOrNull(source) != null
    }
}
