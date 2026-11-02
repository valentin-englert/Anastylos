package org.anastylos.capture.video

import java.io.File
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

@Serializable
private data class PendingEntry(val fileName: String, val sessionId: String, val sha256: String, val size: Long)

/**
 * The recorded videos that have not reached the workstation yet, kept in a
 * small JSON file next to them so an upload resumes after the app restarts.
 */
class PendingVideoStore(private val directory: File) {
    private val file = File(directory, "pending-videos.json")

    @Synchronized
    fun list(): List<PendingVideo> = try {
        Json.decodeFromString(ListSerializer(PendingEntry.serializer()), file.readText())
            .map { PendingVideo(it.fileName, it.sessionId, it.sha256, it.size) }
    } catch (_: Exception) {
        emptyList()
    }

    @Synchronized
    fun add(video: PendingVideo) = write(list().filterNot { it.fileName == video.fileName } + video)

    @Synchronized
    fun remove(video: PendingVideo) = write(list().filterNot { it.fileName == video.fileName })

    private fun write(videos: List<PendingVideo>) {
        directory.mkdirs()
        val partial = File(directory, "pending-videos.json.partial")
        partial.writeText(
            Json.encodeToString(
                ListSerializer(PendingEntry.serializer()),
                videos.map { PendingEntry(it.fileName, it.sessionId, it.sha256, it.size) },
            ),
        )
        if (!partial.renameTo(file)) {
            file.delete()
            check(partial.renameTo(file)) { "could not replace ${file.name}" }
        }
    }
}
