package org.anastylos.capture.video

import com.google.common.truth.Truth.assertThat
import java.io.File
import java.security.MessageDigest
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.anastylos.capture.camera.VideoCamera
import org.anastylos.capture.camera.VideoRecordingEvent
import org.anastylos.capture.sync.GuidedControlClient
import org.anastylos.capture.sync.HttpRequest
import org.anastylos.capture.sync.HttpResponse
import org.anastylos.capture.sync.HttpTransport
import org.anastylos.capture.sync.TransportException
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

private const val SESSION = "11111111-2222-3333-4444-555555555555"
private const val CHUNK = 10L

/** In-memory stand-in for the receiver's video routes (same wire shapes). */
private class FakeReceiver : HttpTransport {
    val chunks = sortedMapOf<Int, ByteArray>()
    var declaredSha = ""
    var declaredSize = 0L
    var status = "receiving"
    var dropOnChunk: Int? = null
    var sentChunks = mutableListOf<Int>()
    var rejectHash = false

    private fun view(): HttpResponse {
        val count = ((declaredSize + CHUNK - 1) / CHUNK).toInt()
        val received = if (status == "complete") "[]" else chunks.keys.joinToString(",", "[", "]")
        val body = """{"video_id":"${declaredSha.take(32)}","status":"$status","chunk_count":$count,"chunk_bytes":$CHUNK,"received":$received}"""
        return HttpResponse(200, body.toByteArray(), emptyMap())
    }

    override fun exchange(request: HttpRequest): HttpResponse {
        assertThat(request.headers["Authorization"]).isEqualTo("Bearer tok")
        if (request.method == "POST") {
            val json = request.body!!.toString(Charsets.UTF_8)
            declaredSha = Regex("\"sha256\":\"([0-9a-f]+)\"").find(json)!!.groupValues[1]
            declaredSize = Regex("\"size\":(\\d+)").find(json)!!.groupValues[1].toLong()
            return view()
        }
        val index = request.path.substringAfterLast('/').toInt()
        if (dropOnChunk == index) {
            dropOnChunk = null
            throw TransportException("connection lost")
        }
        chunks[index] = request.body!!
        sentChunks += index
        val count = ((declaredSize + CHUNK - 1) / CHUNK).toInt()
        if (chunks.size == count) {
            val all = chunks.values.fold(ByteArray(0)) { a, b -> a + b }
            if (rejectHash || sha(all) != declaredSha) {
                return HttpResponse(409, """{"error":{"code":"hash_mismatch","message":"does not match"}}""".toByteArray(), emptyMap())
            }
            status = "complete"
        }
        return view()
    }
}

private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

class VideoUploadTest {
    @get:Rule val folder = TemporaryFolder()
    private val payload = ByteArray(47) { it.toByte() } // five chunks, the last one short

    private fun entry(name: String = "v.mp4", bytes: ByteArray = payload): PendingVideo {
        File(folder.root, name).writeBytes(bytes)
        return PendingVideo(name, SESSION, sha(bytes), bytes.size.toLong())
    }

    private fun context(receiver: FakeReceiver, session: String = SESSION) =
        VideoUploadContext(VideoUploadClient(GuidedControlClient(receiver)), session) { "tok" }

    @Test
    fun uploadsEveryChunkAndEndsComplete() = runTest {
        val receiver = FakeReceiver()
        val progress = mutableListOf<Pair<Int, Int>>()
        val result = VideoUploader(folder.root).upload(entry(), context(receiver)) { s, t -> progress += s to t }
        assertThat(result).isEqualTo(UploadResult.Done)
        assertThat(receiver.sentChunks).containsExactly(0, 1, 2, 3, 4).inOrder()
        assertThat(progress.last()).isEqualTo(5 to 5)
    }

    @Test
    fun aDroppedConnectionResumesWithOnlyTheMissingChunks() = runTest {
        val receiver = FakeReceiver().apply { dropOnChunk = 3 }
        val uploader = VideoUploader(folder.root)
        val video = entry()
        val first = uploader.upload(video, context(receiver)) { _, _ -> }
        assertThat(first).isInstanceOf(UploadResult.Retry::class.java)
        assertThat(receiver.sentChunks).containsExactly(0, 1, 2).inOrder()
        val second = uploader.upload(video, context(receiver)) { _, _ -> }
        assertThat(second).isEqualTo(UploadResult.Done)
        assertThat(receiver.sentChunks).containsExactly(0, 1, 2, 3, 4).inOrder()
    }

    @Test
    fun aCompleteVideoIsNotSentAgain() = runTest {
        val receiver = FakeReceiver()
        val uploader = VideoUploader(folder.root)
        val video = entry()
        uploader.upload(video, context(receiver)) { _, _ -> }
        receiver.sentChunks.clear()
        assertThat(uploader.upload(video, context(receiver)) { _, _ -> }).isEqualTo(UploadResult.Done)
        assertThat(receiver.sentChunks).isEmpty()
    }

    @Test
    fun aHashMismatchIsAPermanentFailureWithTheReceiversReason() = runTest {
        val receiver = FakeReceiver().apply { rejectHash = true }
        val result = VideoUploader(folder.root).upload(entry(), context(receiver)) { _, _ -> }
        assertThat(result).isEqualTo(UploadResult.Failed("does not match"))
    }

    @Test
    fun waitsForTheSessionTheVideoBelongsToAndFailsOnAMissingFile() = runTest {
        val receiver = FakeReceiver()
        val video = entry()
        val other = VideoUploader(folder.root).upload(video, context(receiver, session = "someone-else")) { _, _ -> }
        assertThat(other).isInstanceOf(UploadResult.Retry::class.java)
        File(folder.root, video.fileName).delete()
        assertThat(VideoUploader(folder.root).upload(video, context(receiver)) { _, _ -> }).isInstanceOf(UploadResult.Failed::class.java)
    }

    @Test
    fun theStoreKeepsPendingVideosAcrossInstances() {
        val video = entry()
        PendingVideoStore(folder.root).add(video)
        assertThat(PendingVideoStore(folder.root).list()).containsExactly(video)
        PendingVideoStore(folder.root).remove(video)
        assertThat(PendingVideoStore(folder.root).list()).isEmpty()
    }
}

class RecordingControllerTest {
    @get:Rule val folder = TemporaryFolder()
    private val payload = ByteArray(25) { (it * 3).toByte() }

    private class FakeCamera(private val payload: ByteArray, var startOk: Boolean = true) : VideoCamera {
        var listener: ((VideoRecordingEvent) -> Unit)? = null
        var file: File? = null
        override suspend fun startVideo(output: File, onEvent: (VideoRecordingEvent) -> Unit): Boolean {
            if (!startOk) return false
            file = output
            output.writeBytes(payload)
            listener = onEvent
            return true
        }

        override fun stopVideo() {
            listener?.invoke(VideoRecordingEvent.Finished(file!!))
        }
    }

    private fun TestScope.controller(camera: FakeCamera, receiver: FakeReceiver, attached: Boolean = true): RecordingController {
        val context = VideoUploadContext(VideoUploadClient(GuidedControlClient(receiver)), SESSION) { "tok" }
        return RecordingController(
            directory = folder.root,
            camera = { camera },
            uploadContext = { if (attached) context else null },
            scope = this,
            retryDelayMs = 1_000,
            io = StandardTestDispatcher(testScheduler),
        )
    }

    @Test
    fun recordingNeedsAnAttachedSession() = runTest {
        val controller = controller(FakeCamera(payload), FakeReceiver(), attached = false)
        controller.start()
        assertThat(controller.recording.value).isInstanceOf(RecordingState.Failed::class.java)
    }

    @Test
    fun startStopSavesLocallyThenUploadsAndEndsDone() = runTest {
        val receiver = FakeReceiver()
        val camera = FakeCamera(payload)
        val controller = controller(camera, receiver)
        controller.start()
        advanceUntilIdle()
        assertThat(controller.recording.value).isInstanceOf(RecordingState.Recording::class.java)
        controller.stop()
        advanceUntilIdle()
        assertThat(controller.recording.value).isEqualTo(RecordingState.Idle)
        assertThat(controller.upload.value).isEqualTo(UploadProgress.Done)
        assertThat(camera.file!!.isFile).isTrue() // the local copy stays
        assertThat(receiver.status).isEqualTo("complete")
        assertThat(PendingVideoStore(folder.root).list()).isEmpty()
    }

    @Test
    fun aDroppedConnectionIsRetriedUntilTheVideoArrives() = runTest {
        val receiver = FakeReceiver().apply { dropOnChunk = 1 }
        val controller = controller(FakeCamera(payload), receiver)
        controller.start()
        advanceUntilIdle()
        controller.stop()
        advanceTimeBy(500)
        assertThat(controller.upload.value).isInstanceOf(UploadProgress.Waiting::class.java)
        assertThat(PendingVideoStore(folder.root).list()).hasSize(1)
        advanceUntilIdle()
        assertThat(controller.upload.value).isEqualTo(UploadProgress.Done)
        assertThat(receiver.status).isEqualTo("complete")
    }

    @Test
    fun aCameraThatCannotStartReportsItAndKeepsNothing() = runTest {
        val controller = controller(FakeCamera(payload, startOk = false), FakeReceiver())
        controller.start()
        advanceUntilIdle()
        assertThat(controller.recording.value).isInstanceOf(RecordingState.Failed::class.java)
        assertThat(PendingVideoStore(folder.root).list()).isEmpty()
    }

    @Test
    fun aFailedRecordingIsDiscardedAndDismissable() = runTest {
        val camera = FakeCamera(payload)
        val controller = controller(camera, FakeReceiver())
        controller.start()
        advanceUntilIdle()
        camera.listener!!.invoke(VideoRecordingEvent.Failed("no valid data"))
        assertThat(camera.file!!.exists()).isFalse()
        assertThat((controller.recording.value as RecordingState.Failed).message).contains("no valid data")
        controller.dismissFailure()
        assertThat(controller.recording.value).isEqualTo(RecordingState.Idle)
    }

    @Test
    fun pendingVideosResumeWhenTheSessionIsAttachedAgain() = runTest {
        val receiver = FakeReceiver()
        val camera = FakeCamera(payload)
        var attached = true
        val context = VideoUploadContext(VideoUploadClient(GuidedControlClient(receiver)), SESSION) { "tok" }
        val controller = RecordingController(folder.root, { camera }, { if (attached) context else null }, this,
            retryDelayMs = 1_000, io = StandardTestDispatcher(testScheduler))
        controller.start()
        advanceUntilIdle()
        attached = false
        controller.stop()
        advanceUntilIdle()
        assertThat(PendingVideoStore(folder.root).list()).hasSize(1)
        attached = true
        controller.resumePending()
        advanceUntilIdle()
        assertThat(receiver.status).isEqualTo("complete")
    }
}

class SheetWatchTest {
    @Test
    fun warnsOnlyAfterTheSheetStaysOutOfViewForTheGracePeriod() {
        val watch = SheetWatch(graceMs = 8_000)
        assertThat(watch.onSample(0, sheetVisible = true)).isFalse()
        assertThat(watch.onSample(1_000, sheetVisible = false)).isFalse()
        assertThat(watch.onSample(8_999, sheetVisible = false)).isFalse()
        assertThat(watch.onSample(9_000, sheetVisible = false)).isTrue()
        assertThat(watch.onSample(9_500, sheetVisible = true)).isFalse()
        assertThat(watch.onSample(10_000, sheetVisible = false)).isFalse()
    }
}
