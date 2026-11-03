package org.anastylos.capture.camera

import java.io.File

/** What the camera reports once a recording ends. */
sealed interface VideoRecordingEvent {
    data class Finished(val file: File) : VideoRecordingEvent
    data class Failed(val message: String) : VideoRecordingEvent
}

/**
 * Disassembly-video recording on the guided camera: 1920×1080 H.264 MP4,
 * no audio, focus and exposure as the operator set them for guided capture.
 * [GuidedCameraSource] is the production implementation; the recording
 * controller unit-tests against fakes.
 */
interface VideoCamera {
    /**
     * Binds the video use case ahead of the recording so that focus, exposure and white balance can be
     * locked on the final camera session. False (no throw) when that fails; recording may still work.
     */
    suspend fun prepareVideo(): Boolean = true

    /**
     * Lets AF, AE and AWB converge on the sheet, then holds them until [unlockExposure]. Never throws;
     * a lock that cannot be honoured is reported in the result and recording goes on.
     */
    suspend fun lockExposure(): ExposureLockResult = ExposureLockResult.failed("exposure lock is not available")

    /** Gives 3A back to the camera. Safe to call without a lock. */
    fun unlockExposure() {}

    /** False (no throw) when the camera cannot record; later results arrive as [VideoRecordingEvent]. */
    suspend fun startVideo(output: File, onEvent: (VideoRecordingEvent) -> Unit): Boolean

    /** Ends the running recording; the file is final once [VideoRecordingEvent.Finished] arrives. */
    fun stopVideo()
}
