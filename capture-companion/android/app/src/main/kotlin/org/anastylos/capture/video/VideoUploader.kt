package org.anastylos.capture.video

import java.io.File
import java.io.RandomAccessFile

/** A recorded video waiting to reach the workstation. */
data class PendingVideo(val fileName: String, val sessionId: String, val sha256: String, val size: Long)

/** Everything an upload needs from the attached guided session. */
class VideoUploadContext(
    val client: VideoUploadClient,
    val sessionId: String,
    val token: suspend () -> String?,
)

sealed interface UploadResult {
    data object Done : UploadResult

    /** Connection trouble or a busy receiver: call again, finished chunks are kept. */
    data class Retry(val reason: String) : UploadResult

    /** The workstation refused the video; resending the same bytes cannot help. */
    data class Failed(val message: String) : UploadResult
}

/** Sends one video in the chunks the receiver asks for, skipping what it already holds. */
class VideoUploader(private val directory: File) {
    suspend fun upload(
        video: PendingVideo,
        context: VideoUploadContext,
        onProgress: (sent: Int, total: Int) -> Unit,
    ): UploadResult {
        if (video.sessionId != context.sessionId) return UploadResult.Retry("waiting for the session this video belongs to")
        val file = File(directory, video.fileName)
        if (!file.isFile || file.length() != video.size) return UploadResult.Failed("The recorded file is missing or changed.")
        val token = context.token() ?: return UploadResult.Retry("not signed in to the workstation yet")
        var status = when (val declared = context.client.declare(token, video.sessionId, video.sha256, video.size)) {
            is VideoCall.Ok -> declared.value
            is VideoCall.Retry -> return UploadResult.Retry(declared.reason)
            is VideoCall.Rejected -> return UploadResult.Failed(declared.message)
        }
        if (status.complete) {
            onProgress(status.chunkCount, status.chunkCount)
            return UploadResult.Done
        }
        val chunkBytes = status.chunkBytes
        if (chunkBytes <= 0 || status.chunkCount <= 0) return UploadResult.Failed("The workstation sent no chunk plan.")
        onProgress(status.received.size, status.chunkCount)
        RandomAccessFile(file, "r").use { source ->
            for (index in 0 until status.chunkCount) {
                if (index in status.received) continue
                val offset = index * chunkBytes
                val bytes = ByteArray(minOf(chunkBytes, video.size - offset).toInt())
                source.seek(offset)
                source.readFully(bytes)
                status = when (val sent = context.client.putChunk(token, video.sessionId, status.videoId, index, bytes)) {
                    is VideoCall.Ok -> sent.value
                    is VideoCall.Retry -> return UploadResult.Retry(sent.reason)
                    is VideoCall.Rejected -> return UploadResult.Failed(sent.message)
                }
                onProgress(status.received.size.coerceAtLeast(index + 1).coerceAtMost(status.chunkCount), status.chunkCount)
            }
        }
        return if (status.complete) UploadResult.Done else UploadResult.Retry("the workstation is still assembling the video")
    }
}
