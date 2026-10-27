package org.anastylos.capture.sync

import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.delay
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.Transport
import org.anastylos.capture.domain.nowUtc
import org.anastylos.capture.domain.sha256Hex

/**
 * In-session frame uploader: oldest pending observation first, one
 * single-observation package per frame, sequential uploads.
 *
 * Idempotency without server round-trips: the package id derives
 * deterministically from the observation *content* (never from random).
 * A retry therefore reuses the exact id the receiver already knows and
 * collapses into its duplicate path, while an edited observation
 * packages under a fresh id so staged bytes can never go stale.
 *
 * Marking rule: [EvidenceRepository.markSynced] runs only after a
 * validated workstation ack that echoes the uploaded package id.
 * Anything else — rejection, transport failure, plan block — leaves
 * the observation pending and retryable. Nothing is ever deleted.
 *
 * Slice A scope: frames are write-once captures. An observation already
 * carrying a SYNC record is skipped; re-sync after a metadata edit is a
 * defined follow-up, not silent behavior.
 */
sealed interface FrameOutcome {
    data class Synced(val packageId: String, val duplicate: Boolean) : FrameOutcome
    /** Validator refused the bytes; not retried within this run. */
    data class Rejected(val reason: String) : FrameOutcome
    /** Retryable failure after exhausting attempts; stays pending. */
    data class Failed(val reason: String) : FrameOutcome
    /** Local plan block (missing bytes, hash mismatch); stays pending. */
    data class Blocked(val reason: String) : FrameOutcome
}

data class UploadRun(
    val outcomes: Map<String, FrameOutcome>,
)

class FrameUploader(
    private val repo: EvidenceRepository,
    private val resolveOriginal: (imageId: String) -> ByteArray?,
    private val client: ReceiverClient,
    private val token: String,
    private val appVersion: String,
    private val clock: () -> Instant = ::nowUtc,
    private val maxAttempts: Int = 5,
    private val baseDelayMillis: Long = 1_000,
    private val maxDelayMillis: Long = 30_000,
    private val sleeper: suspend (Long) -> Unit = { delay(it) },
) {
    suspend fun uploadSession(sessionId: String): UploadRun {
        val session = requireNotNull(repo.getSession(sessionId)) { "unknown session: $sessionId" }
        val pending = repo.getAllObservations()
            .filter { it.sessionId == sessionId && it.exportHistory.none { r -> r.via == Transport.SYNC } }
            .sortedBy { it.capturedAt }
        val outcomes = linkedMapOf<String, FrameOutcome>()
        for (obs in pending) {
            val packageId = framePackageId(obs)
            val built = try {
                buildFramePackage(session, listOf(obs), resolveOriginal, appVersion, packageId)
            } catch (e: PackageBuildException) {
                outcomes[obs.observationId] = FrameOutcome.Blocked(e.message ?: "export blocked")
                continue
            }
            var attempt = 0
            var done = false
            while (!done) {
                attempt++
                val outcome = try {
                    client.uploadPackage(token, built)
                } catch (e: TransportException) {
                    null
                }
                when (outcome) {
                    is UploadOutcome.Staged -> {
                        repo.markSynced(listOf(obs.observationId), packageId, clock())
                        outcomes[obs.observationId] =
                            FrameOutcome.Synced(packageId, outcome.duplicate)
                        done = true
                    }
                    is UploadOutcome.Rejected ->
                        done = reject(outcomes, obs, "workstation refused the package")
                    is UploadOutcome.Unauthorized -> {
                        // Auth is broken for every frame: abort the run, fail all
                        // remaining pending observations explicitly, mark nothing.
                        val reason = "unauthorized: ${outcome.message}"
                        outcomes[obs.observationId] = FrameOutcome.Failed(reason)
                        for (rest in pending) {
                            outcomes.putIfAbsent(rest.observationId, FrameOutcome.Failed(reason))
                        }
                        return UploadRun(outcomes)
                    }
                    is UploadOutcome.TooLarge ->
                        done = reject(outcomes, obs, "package exceeds the size cap")
                    is UploadOutcome.RateLimited -> {
                        if (attempt >= maxAttempts) {
                            outcomes[obs.observationId] =
                                FrameOutcome.Failed("rate-limited after $attempt attempts")
                            done = true
                        } else {
                            sleeper(serverDelay(outcome.retryAfterSeconds, attempt))
                        }
                    }
                    is UploadOutcome.Failed, null -> {
                        if (attempt >= maxAttempts) {
                            val reason = (outcome as? UploadOutcome.Failed)?.message
                                ?: "transfer interrupted"
                            outcomes[obs.observationId] =
                                FrameOutcome.Failed("$reason after $attempt attempts")
                            done = true
                        } else {
                            sleeper(backoff(attempt))
                        }
                    }
                }
            }
        }
        return UploadRun(outcomes)
    }

    private fun reject(
        outcomes: MutableMap<String, FrameOutcome>,
        obs: ComponentObservation,
        reason: String,
    ): Boolean {
        outcomes[obs.observationId] = FrameOutcome.Rejected(reason)
        return true
    }

    private fun backoff(attempt: Int): Long =
        minOf(baseDelayMillis * (1L shl (attempt - 1)), maxDelayMillis)

    private fun serverDelay(retryAfterSeconds: Long?, attempt: Int): Long {
        val hinted = retryAfterSeconds?.coerceIn(1, 300)?.times(1_000)
        return minOf(hinted ?: backoff(attempt), maxDelayMillis)
    }
}

/**
 * Content-derived package id: stable across retries and restarts for
 * identical evidence, fresh after any edit. Export history is excluded
 * (it records past uploads; it must not perturb the bytes' identity).
 */
fun framePackageId(obs: ComponentObservation): String {
    val parts = mutableListOf(
        "anastylos-frame-package-v1",
        obs.observationId,
        obs.sessionId,
        obs.capturedAt.toString(),
        obs.captureSource.name,
        obs.reviewState.name,
        obs.componentReference.orEmpty(),
        obs.trayReference.orEmpty(),
        obs.operatorNotes.orEmpty(),
    )
    for (m in obs.markings) parts += "${m.text}\u0000${m.state.name}"
    parts += "${obs.category.value}\u0000${obs.category.state.name}"
    for (m in obs.measurements) parts += "${m.label}\u0000${m.value}\u0000${m.unit}\u0000${m.provenance.name}"
    parts += "${obs.quantity.value}\u0000${obs.quantity.state.name}"
    for (c in obs.candidates) {
        parts += listOf(
            c.rank.toString(), c.label, c.confidence.toString(),
            c.supportingObservations.joinToString(","), c.uncertainty.orEmpty(),
            c.provider, c.model,
        ).joinToString("\u0000")
    }
    for (image in obs.images.sortedBy { it.imageId }) {
        parts += listOf(
            image.imageId, image.role.name, image.filename, image.mime,
            image.byteLength.toString(), image.sha256, image.capturedAt.toString(),
            image.crop?.let { "${it.x},${it.y},${it.width},${it.height}" }.orEmpty(),
        ).joinToString("\u0000")
    }
    val fingerprint = parts.joinToString("\u0001").toByteArray(Charsets.UTF_8).sha256Hex()
    return UUID.nameUUIDFromBytes(fingerprint.toByteArray(Charsets.UTF_8)).toString()
}
