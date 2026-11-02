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
    /** False (no throw) when the camera cannot record; later results arrive as [VideoRecordingEvent]. */
    suspend fun startVideo(output: File, onEvent: (VideoRecordingEvent) -> Unit): Boolean

    /** Ends the running recording; the file is final once [VideoRecordingEvent.Finished] arrives. */
    fun stopVideo()
}
