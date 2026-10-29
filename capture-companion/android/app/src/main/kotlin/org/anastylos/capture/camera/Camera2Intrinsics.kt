package org.anastylos.capture.camera

import android.hardware.camera2.CameraCharacteristics
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.Camera
import org.anastylos.capture.guidance.PoseEstimator

/**
 * Factory (uncalibrated) intrinsics for the bound analysis stream,
 * derived from CameraCharacteristics at zoom 1.0 with zero
 * distortion. These enable real detection and coarse PnP on device,
 * but they are NEVER a calibrated profile: `profileId` carries the
 * `-uncalibrated` suffix, and metric evidence requires a measured
 * calibration profile (Wave-2 calibration session). Any missing
 * characteristic or geometry mismatch yields null (detection-only).
 */
object Camera2Intrinsics {
    @ExperimentalCamera2Interop
    fun forAnalysisStream(camera: Camera, analysisWidth: Int, analysisHeight: Int): PoseEstimator.Intrinsics? {
        if (analysisWidth <= 0 || analysisHeight <= 0) return null
        return try {
            read(camera, analysisWidth, analysisHeight)
        } catch (_: Exception) {
            null
        }
    }

    @ExperimentalCamera2Interop
    private fun read(camera: Camera, analysisWidth: Int, analysisHeight: Int): PoseEstimator.Intrinsics? {
        val info = Camera2CameraInfo.from(camera.cameraInfo)
        val focalMm = info.getCameraCharacteristic(
            CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS,
        )?.firstOrNull() ?: return null
        val sensorMm = info.getCameraCharacteristic(
            CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE,
        ) ?: return null
        val activeArray = info.getCameraCharacteristic(
            CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE,
        ) ?: return null
        if (focalMm <= 0f || sensorMm.width <= 0f || sensorMm.height <= 0f) return null
        if (activeArray.width() <= 0 || activeArray.height() <= 0) return null
        // Full-array readout at zoom 1.0: focal pixels scale with the
        // analysis stream; the optical center is the array center
        // (factory approximation, documented above).
        val fx = focalMm * analysisWidth / sensorMm.width
        val fy = focalMm * analysisHeight / sensorMm.height
        if (!fx.isFinite() || !fy.isFinite() || fx <= 0f || fy <= 0f) return null
        return PoseEstimator.Intrinsics(
            fx = fx.toDouble(),
            fy = fy.toDouble(),
            cx = (analysisWidth - 1) / 2.0,
            cy = (analysisHeight - 1) / 2.0,
            width = analysisWidth,
            height = analysisHeight,
            distortion = listOf(0.0, 0.0, 0.0, 0.0, 0.0),
            profileId = "factory-${info.cameraId}-uncalibrated",
        )
    }
}
