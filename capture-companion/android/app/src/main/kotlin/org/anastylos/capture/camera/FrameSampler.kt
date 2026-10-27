package org.anastylos.capture.camera

import java.time.Instant
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.files.EvidenceFileStore
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.ImageRole
import org.anastylos.capture.domain.MAX_PHOTO_BYTES
import org.anastylos.capture.domain.newId
import org.anastylos.capture.domain.nowUtc
import org.anastylos.capture.domain.sha256Hex

/**
 * Continuous still sampling for in-session evidence.
 *
 * Each tick takes one full CameraX still (never a preview screenshot),
 * persists the original bytes atomically, hashes them, and files the
 * frame as its own single-image observation. Capture failures are
 * reported as gaps and the loop continues: a missed frame must never
 * stop the session or corrupt earlier evidence.
 *
 * The [FrameSampler] core is pure JVM (repository + file store +
 * injected still source) and fully unit-tested; only [CameraStillSource]
 * touches CameraX and runs solely on-device.
 */
data class SamplingConfig(
    /** Seconds between shutter ticks. Bounded: 1s..300s. */
    val intervalSeconds: Int = 5,
) {
    init {
        require(intervalSeconds in 1..300) { "sampling interval must be 1..300 seconds" }
    }
}

data class CapturedStill(
    val bytes: ByteArray,
    val capturedAt: Instant,
)

sealed interface SamplerEvent {
    data class FrameStored(
        val observationId: String,
        val imageId: String,
        val capturedAt: Instant,
        val byteLength: Long,
    ) : SamplerEvent

    /** A tick produced no frame (camera error, adopted refusal). */
    data class FrameMissed(
        val capturedAt: Instant,
        val reason: String,
    ) : SamplerEvent
}

class FrameAdoptException(message: String) : IllegalArgumentException(message)

class FrameSampler(
    private val repo: EvidenceRepository,
    private val files: EvidenceFileStore,
    private val clock: () -> Instant = ::nowUtc,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) {
    /**
     * Files one captured still as a new single-image frame observation.
     * Either the whole frame lands (row + bytes + hash) or nothing does:
     * a failure after the observation row was created removes the row and
     * any landed bytes again, so failed ticks leave no litter at all.
     */
    suspend fun adoptFrame(
        sessionId: String,
        still: CapturedStill,
        sequence: Long,
    ): SamplerEvent.FrameStored {
        if (still.bytes.isEmpty()) throw FrameAdoptException("empty capture bytes")
        if (still.bytes.size.toLong() > MAX_PHOTO_BYTES) {
            throw FrameAdoptException(
                "Photo is ${(still.bytes.size / 1048576.0)} MB; " +
                    "the per-photo cap is ${MAX_PHOTO_BYTES / 1048576} MB.",
            )
        }
        if (!isJpeg(still.bytes)) {
            throw FrameAdoptException("capture bytes are not a JPEG still")
        }
        val obs = repo.createObservation(sessionId, CaptureSource.CAMERA)
        val imageId = newId()
        try {
            val filename = frameFilename(still.capturedAt, sequence, imageId)
            files.storeOriginal(still.bytes, obs.observationId, imageId, "jpg")
            val image = EvidenceImage(
                imageId = imageId,
                observationId = obs.observationId,
                role = ImageRole.OTHER,
                filename = filename,
                mime = "image/jpeg",
                byteLength = still.bytes.size.toLong(),
                sha256 = still.bytes.sha256Hex(),
                capturedAt = still.capturedAt,
                crop = null,
            )
            repo.recordImage(image)
            // Best effort off the caller thread: a missing preview never
            // fails the frame.
            try {
                withContext(ioDispatcher) {
                    files.makePreview(
                        files.originalFile(obs.observationId, imageId, "jpg"),
                        files.previewFile(obs.observationId, imageId),
                    )
                }
            } catch (_: Exception) {
            }
            return SamplerEvent.FrameStored(
                obs.observationId,
                imageId,
                still.capturedAt,
                still.bytes.size.toLong(),
            )
        } catch (e: Exception) {
            if (e is CancellationException) throw e
            try {
                files.deleteImageFiles(obs.observationId, imageId)
            } catch (_: Exception) {
            }
            try {
                repo.deleteObservation(obs.observationId)
            } catch (_: Exception) {
            }
            throw e
        }
    }

    /**
     * Runs the sampling loop until cancelled: capture, adopt, report,
     * wait. Capture and adopt failures surface as [SamplerEvent.FrameMissed]
     * and the loop continues with the next tick.
     */
    suspend fun runSampling(
        sessionId: String,
        config: SamplingConfig,
        captureStill: suspend () -> CapturedStill,
        onEvent: (SamplerEvent) -> Unit,
    ) {
        var sequence = 0L
        while (currentCoroutineContext().isActive) {
            sequence++
            try {
                val still = captureStill()
                currentCoroutineContext().ensureActive()
                onEvent(adoptFrame(sessionId, still, sequence))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                onEvent(SamplerEvent.FrameMissed(clock(), e.message ?: "capture failed"))
            }
            delay(config.intervalSeconds * 1_000L)
        }
    }
}

fun isJpeg(bytes: ByteArray): Boolean =
    bytes.size >= 3 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte() && bytes[2] == 0xFF.toByte()

/**
 * Sortable, unique-within-session frame name. The short image-id suffix
 * disambiguates ticks that land inside the same millisecond.
 */
fun frameFilename(capturedAt: Instant, sequence: Long, imageId: String): String {
    val stamp = capturedAt.toString().replace(Regex("[:.]"), "")
    return "frame-%s-%04d-%s.jpg".format(stamp, sequence % 10_000, imageId.take(8))
}

/** Lifecycle helper: starts [FrameSampler.runSampling] as a child job. */
fun FrameSampler.launchSampling(
    scope: kotlinx.coroutines.CoroutineScope,
    sessionId: String,
    config: SamplingConfig,
    captureStill: suspend () -> CapturedStill,
    onEvent: (SamplerEvent) -> Unit,
): Job = scope.launch { runSampling(sessionId, config, captureStill, onEvent) }
