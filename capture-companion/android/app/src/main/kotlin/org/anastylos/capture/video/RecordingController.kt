package org.anastylos.capture.video

import java.io.File
import java.security.MessageDigest
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.anastylos.capture.camera.VideoCamera
import org.anastylos.capture.camera.VideoRecordingEvent

sealed interface RecordingState {
    data object Idle : RecordingState
    data class Recording(val startedAtMs: Long) : RecordingState
    data class Failed(val message: String) : RecordingState
}

/** Where the newest recording is on its way to the workstation. */
sealed interface UploadProgress {
    data class Sending(val sent: Int, val total: Int) : UploadProgress
    data class Waiting(val reason: String) : UploadProgress
    data object Done : UploadProgress
    data class Failed(val message: String) : UploadProgress
}

/**
 * Record the disassembly, save it locally first, then send it to the
 * workstation in chunks, picking up where it left off after any dropout.
 * Recording never waits for the network and never stops by itself.
 */
class RecordingController(
    private val directory: File,
    private val camera: () -> VideoCamera?,
    private val uploadContext: () -> VideoUploadContext?,
    private val scope: CoroutineScope,
    private val store: PendingVideoStore = PendingVideoStore(directory),
    private val uploader: VideoUploader = VideoUploader(directory),
    private val clockMs: () -> Long = System::currentTimeMillis,
    private val retryDelayMs: Long = 5_000L,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) {
    private val _recording = MutableStateFlow<RecordingState>(RecordingState.Idle)
    val recording: StateFlow<RecordingState> = _recording.asStateFlow()

    private val _upload = MutableStateFlow<UploadProgress?>(null)
    val upload: StateFlow<UploadProgress?> = _upload.asStateFlow()

    private var uploadJob: Job? = null
    private var recordingSessionId: String? = null

    fun start() {
        if (_recording.value is RecordingState.Recording) return
        val context = uploadContext()
        if (context == null) {
            _recording.value = RecordingState.Failed("Attach a session first, then record.")
            return
        }
        val cam = camera()
        if (cam == null) {
            _recording.value = RecordingState.Failed("The camera is not ready.")
            return
        }
        val file = File(directory, "disassembly-${clockMs()}.mp4")
        directory.mkdirs()
        recordingSessionId = context.sessionId
        _recording.value = RecordingState.Recording(clockMs())
        scope.launch {
            val started = try {
                cam.startVideo(file) { event -> onCameraEvent(file, event) }
            } catch (e: Exception) {
                false
            }
            if (!started && _recording.value is RecordingState.Recording) {
                _recording.value = RecordingState.Failed("The camera could not start recording.")
            }
        }
    }

    fun stop() {
        if (_recording.value !is RecordingState.Recording) return
        camera()?.stopVideo()
    }

    /** Picks up videos that are still waiting, e.g. after a restart or when a session is attached again. */
    fun resumePending() {
        if (uploadJob?.isActive == true || store.list().isEmpty()) return
        uploadJob = scope.launch { uploadLoop() }
    }

    fun dismissFailure() {
        if (_recording.value is RecordingState.Failed) _recording.value = RecordingState.Idle
        if (_upload.value is UploadProgress.Failed) _upload.value = null
    }

    private fun onCameraEvent(file: File, event: VideoRecordingEvent) {
        when (event) {
            is VideoRecordingEvent.Finished -> scope.launch { saved(event.file) }
            is VideoRecordingEvent.Failed -> {
                file.delete()
                _recording.update { RecordingState.Failed("Recording stopped: ${event.message}") }
            }
        }
    }

    private suspend fun saved(file: File) {
        val sessionId = recordingSessionId
        val entry = withContext(io) {
            if (sessionId == null || !file.isFile || file.length() == 0L) null
            else PendingVideo(file.name, sessionId, sha256(file), file.length())
        }
        if (entry == null) {
            _recording.value = RecordingState.Failed("The recording could not be saved.")
            return
        }
        store.add(entry)
        _recording.value = RecordingState.Idle
        resumePending()
    }

    private suspend fun uploadLoop() {
        while (scope.isActive) {
            val next = store.list().firstOrNull() ?: return
            val context = uploadContext()
            if (context == null) {
                _upload.value = UploadProgress.Waiting("Attach the session to send the video.")
                return
            }
            val result = withContext(io) {
                uploader.upload(next, context) { sent, total -> _upload.value = UploadProgress.Sending(sent, total) }
            }
            when (result) {
                UploadResult.Done -> {
                    store.remove(next)
                    _upload.value = UploadProgress.Done
                }
                is UploadResult.Retry -> {
                    _upload.value = UploadProgress.Waiting(result.reason)
                    delay(retryDelayMs)
                }
                is UploadResult.Failed -> {
                    _upload.value = UploadProgress.Failed(result.message)
                    return
                }
            }
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1 shl 20)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}

/** Warns when the sheet stays out of view for a while during a recording; it never stops anything. */
class SheetWatch(private val graceMs: Long = 8_000L) {
    private var lostSinceMs: Long? = null

    /** True while the warning should show. */
    fun onSample(nowMs: Long, sheetVisible: Boolean): Boolean {
        if (sheetVisible) {
            lostSinceMs = null
            return false
        }
        val since = lostSinceMs ?: nowMs.also { lostSinceMs = it }
        return nowMs - since >= graceMs
    }

    fun reset() {
        lostSinceMs = null
    }
}
