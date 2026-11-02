package org.anastylos.capture.sync

import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.GuidedAssociationRepository
import org.anastylos.capture.domain.nowUtc
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.ReceiptKind

/**
 * Guided-delivery orchestration for one captured image: package,
 * attempt/asset binding, guided upload (joins the receipt), then the
 * spatial sidecar; manual captures use the legacy upload only — recording every stage in the association row.
 *
 * Idempotency: the package id is content-derived (stable across
 * retries and restarts); attempt and operation ids are generated
 * once per [deliver] run and reused across in-run retries. A retry
 * never re-uploads a marked package and never re-binds an
 * acknowledged attempt blindly: each stage checks the recorded
 * state first. Marking rule (same as [FrameUploader]): local
 * records advance only on validated Core acks that echo our ids.
 */
data class DeliveryInput(
    val imageId: String,
    val legacyToken: String,
    val guidedToken: String,
    val guidedSessionId: String,
    /** Null when no pose exists: the package path still runs. */
    val sidecar: SpatialSidecar.Input?,
)

sealed interface DeliveryOutcome {
    data class Delivered(
        val deliveryState: DeliveryState,
        val receiptStatus: ReceiptKind?,
        /** True when the sidecar landed ambiguously (revision conflict). */
        val sidecarUncertain: Boolean = false,
        val sidecarSkippedReason: String? = null,
    ) : DeliveryOutcome
    /** Validator refused the bytes; terminal for this package. */
    data class PackageRejected(val reason: String) : DeliveryOutcome
    /** Upload retryable-but-exhausted; association stays RETRY_WAIT. */
    data class UploadFailed(val reason: String) : DeliveryOutcome
    data class BindingFailed(val reason: String) : DeliveryOutcome
    data class SidecarFailed(val reason: String) : DeliveryOutcome
    data class AuthLost(val reason: String) : DeliveryOutcome
    /** Local plan block (unknown ids, missing bytes); stays pending. */
    data class Blocked(val reason: String) : DeliveryOutcome
}

class GuidedDelivery(
    private val guided: GuidedControlClient,
    private val packages: ReceiverClient,
    private val repo: EvidenceRepository,
    private val associations: GuidedAssociationRepository,
    private val readOriginal: (observationId: String, imageId: String, ext: String) -> ByteArray?,
    private val appVersion: String,
    private val clock: () -> Instant = ::nowUtc,
    private val uuid: () -> String = { UUID.randomUUID().toString() },
    private val maxAttempts: Int = 3,
    private val sleeper: suspend (Long) -> Unit = { delay(it) },
    private val ioDispatcher: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.IO,
) {
    suspend fun deliver(input: DeliveryInput): DeliveryOutcome {
        require(input.legacyToken.isNotBlank()) { "legacy token must not be blank" }
        require(input.guidedToken.isNotBlank()) { "guided token must not be blank" }
        val association = associations.getGuidedAssociation(input.imageId)
            ?: return DeliveryOutcome.Blocked("no guided association for image ${input.imageId}")
        val observation = repo.getObservation(association.observationId)
            ?: return DeliveryOutcome.Blocked("unknown observation ${association.observationId}")
        val session = repo.getSession(observation.sessionId)
            ?: return DeliveryOutcome.Blocked("unknown session ${observation.sessionId}")
        if (association.guidedSessionId != input.guidedSessionId) {
            return DeliveryOutcome.Blocked("association belongs to another guided session")
        }

        // -- stage 1: package + upload (skipped when already marked) --
        val packageId = association.packageId ?: framePackageId(observation)
        val alreadyMarked = observation.exportHistory.any {
            it.packageId == packageId && it.via == org.anastylos.capture.domain.Transport.SYNC
        }
        var current = association
        val resolveOriginal: (String) -> ByteArray? = { imageId ->
            observation.images.firstOrNull { it.imageId == imageId }?.let { image ->
                readOriginal(
                    observation.observationId, imageId,
                    image.filename.substringAfterLast('.', ""),
                )
            }
        }
        val attemptId = current.attemptId
        if (attemptId == null) {
            // -- manual capture: legacy upload, no attempt, no receipt --
            if (!alreadyMarked) {
                val built = try {
                    buildFramePackage(session, listOf(observation), resolveOriginal, appVersion, packageId)
                } catch (e: PackageBuildException) {
                    return DeliveryOutcome.Blocked(e.message ?: "export blocked")
                }
                uploadStage(input.legacyToken, built, null, observation.observationId, current)
                    .let { (stage, recorded) ->
                        current = recorded
                        stage?.let { return it }
                    }
            } else if (current.packageId == null || current.deliveryState == DeliveryState.PENDING_PACKAGE ||
                current.deliveryState == DeliveryState.UPLOADING || current.deliveryState == DeliveryState.RETRY_WAIT
            ) {
                current = record(current.copy(packageId = packageId, deliveryState = DeliveryState.PACKAGE_ACCEPTED))
            }
            return DeliveryOutcome.Delivered(
                current.deliveryState, current.receiptStatus,
                sidecarSkippedReason = "no attempt reserved (manual capture)",
            )
        }

        // -- G0 order for an attempt: package → bind → guided upload → sidecar.
        // The package bytes are deterministic, so a rebuild after a restart
        // or an earlier legacy upload yields the same id and sha.
        val built = try {
            buildFramePackage(session, listOf(observation), resolveOriginal, appVersion, packageId)
        } catch (e: PackageBuildException) {
            return DeliveryOutcome.Blocked(e.message ?: "export blocked")
        }
        val image = observation.images.firstOrNull { it.imageId == input.imageId }
            ?: return DeliveryOutcome.Blocked("unknown image ${input.imageId}")
        val binding = GuidedAssetBinding(
            operationId = uuid(),
            attemptId = attemptId,
            packageId = packageId,
            observationId = observation.observationId,
            imageId = image.imageId,
            imageSha256 = image.sha256,
            packageSha256 = built.packageSha256,
        )
        val bound = bindWithRetry(input.guidedToken, binding)
        when (bound) {
            is BindStage.Bound -> Unit
            is BindStage.AuthLost -> return DeliveryOutcome.AuthLost(bound.reason)
            is BindStage.Failed -> return DeliveryOutcome.BindingFailed(bound.reason)
        }
        if (current.receiptStatus == null) {
            val requestId = current.requestId
            val requestRevision = current.requestRevision
            if (requestId == null || requestRevision == null) {
                return DeliveryOutcome.Blocked("attempt $attemptId has no request link")
            }
            val link = GuidedUploadLink(input.guidedSessionId, requestId, requestRevision, attemptId)
            uploadStage(input.guidedToken, built, link, observation.observationId, current)
                .let { (stage, recorded) ->
                    current = recorded
                    stage?.let { return it }
                }
        }

        // -- stage 3: spatial sidecar (skipped without a pose) --
        val sidecarInput = input.sidecar
        if (sidecarInput == null) {
            return DeliveryOutcome.Delivered(
                current.deliveryState, current.receiptStatus,
                sidecarSkippedReason = "no pose available for a sidecar",
            )
        }
        val builtSidecar = SpatialSidecar.build(sidecarInput)
        val sidecarJson = builtSidecar.json
        if (sidecarJson == null) {
            return DeliveryOutcome.Delivered(
                current.deliveryState, current.receiptStatus,
                sidecarSkippedReason = builtSidecar.blockedReasons.firstOrNull()
                    ?: "sidecar blocked",
            )
        }
        val sidecarOpId = uuid()
        val uploaded = sidecarWithRetry(
            input.guidedToken, attemptId, sidecarOpId, current.sidecarRevision, sidecarJson,
        )
        return when (uploaded) {
            is SidecarStage.Landed -> {
                val receiptKind = uploaded.receiptStatus?.let { wire ->
                    ReceiptKind.entries.firstOrNull { it.wire == wire }
                }
                val delivery = if (uploaded.receiptStatus != null && receiptKind != null) {
                    mapReceiptToDelivery(uploaded.receiptStatus) ?: DeliveryState.SPATIAL_PENDING
                } else {
                    DeliveryState.SPATIAL_PENDING
                }
                current = record(
                    current.copy(
                        deliveryState = delivery,
                        receiptStatus = receiptKind ?: current.receiptStatus,
                        sidecarRevision = current.sidecarRevision + 1,
                    ),
                )
                DeliveryOutcome.Delivered(current.deliveryState, current.receiptStatus)
            }
            is SidecarStage.Uncertain -> {
                current = record(current.copy(deliveryState = DeliveryState.SPATIAL_PENDING))
                DeliveryOutcome.Delivered(
                    current.deliveryState, current.receiptStatus, sidecarUncertain = true,
                )
            }
            is SidecarStage.AuthLost -> DeliveryOutcome.AuthLost(uploaded.reason)
            is SidecarStage.Failed -> {
                record(current.copy(deliveryState = DeliveryState.RETRY_WAIT))
                DeliveryOutcome.SidecarFailed(uploaded.reason)
            }
        }
    }

    /**
     * Uploads [built] (guided when [link] is set) and records the outcome.
     * Returns a terminal outcome to stop delivery, or null to continue.
     */
    private suspend fun uploadStage(
        token: String,
        built: BuiltFramePackage,
        link: GuidedUploadLink?,
        observationId: String,
        association: org.anastylos.capture.data.GuidedAssociation,
    ): Pair<DeliveryOutcome?, org.anastylos.capture.data.GuidedAssociation> {
        var current = record(association.copy(deliveryState = DeliveryState.UPLOADING))
        return when (val uploaded = uploadWithRetry(token, built, link)) {
            is UploadStage.Staged -> {
                repo.markSynced(listOf(observationId), built.packageId, clock())
                val receipt = uploaded.receiptStatus?.let { wire -> ReceiptKind.entries.firstOrNull { it.wire == wire } }
                current = record(
                    current.copy(
                        packageId = built.packageId,
                        deliveryState = uploaded.receiptStatus?.let { mapReceiptToDelivery(it) }
                            ?: DeliveryState.PACKAGE_ACCEPTED,
                        receiptStatus = receipt ?: current.receiptStatus,
                    ),
                )
                null to current
            }
            is UploadStage.Rejected -> {
                record(current.copy(deliveryState = DeliveryState.REJECTED))
                DeliveryOutcome.PackageRejected(uploaded.reason) to current
            }
            is UploadStage.AuthLost -> DeliveryOutcome.AuthLost(uploaded.reason) to current
            is UploadStage.Failed -> {
                current = record(current.copy(deliveryState = DeliveryState.RETRY_WAIT))
                DeliveryOutcome.UploadFailed(uploaded.reason) to current
            }
        }
    }

    private sealed interface UploadStage {
        data class Staged(val receiptStatus: String?) : UploadStage
        data class Rejected(val reason: String) : UploadStage
        data class AuthLost(val reason: String) : UploadStage
        data class Failed(val reason: String) : UploadStage
    }

    private suspend fun uploadWithRetry(token: String, pkg: BuiltFramePackage, link: GuidedUploadLink?): UploadStage {
        var attempt = 0
        while (true) {
            attempt++
            val outcome = try {
                packages.uploadPackage(token, pkg, link)
            } catch (_: TransportException) {
                null
            } catch (_: Exception) {
                null
            }
            when (outcome) {
                is UploadOutcome.Staged -> return UploadStage.Staged(outcome.receiptStatus)
                is UploadOutcome.Rejected -> return UploadStage.Rejected("workstation refused the package")
                is UploadOutcome.Unauthorized -> return UploadStage.AuthLost("unauthorized: ${outcome.message}")
                is UploadOutcome.TooLarge -> return UploadStage.Rejected("package exceeds the size cap")
                is UploadOutcome.RateLimited, is UploadOutcome.Failed, null -> {
                    if (attempt >= maxAttempts) {
                        val reason = (outcome as? UploadOutcome.Failed)?.message ?: "transfer interrupted"
                        return UploadStage.Failed("$reason after $attempt attempts")
                    }
                    sleeper(backoff(attempt))
                }
            }
        }
    }

    private sealed interface BindStage {
        data object Bound : BindStage
        data class AuthLost(val reason: String) : BindStage
        data class Failed(val reason: String) : BindStage
    }

    private suspend fun bindWithRetry(token: String, binding: GuidedAssetBinding): BindStage {
        var attempt = 0
        while (true) {
            attempt++
            val outcome = try {
                guided.bindAsset(token, binding.attemptId, binding)
            } catch (_: Exception) {
                null
            }
            when (outcome) {
                is GuidedOutcome.Success ->
                    if (outcome.value.status == "asset_bound") {
                        return BindStage.Bound
                    } else {
                        return BindStage.Failed("binding not acknowledged: ${outcome.value.status}")
                    }
                is GuidedOutcome.Unauthorized -> return BindStage.AuthLost("unauthorized: ${outcome.message}")
                is GuidedOutcome.Conflict ->
                    return BindStage.Failed("binding conflict (${outcome.code}): ${outcome.message}")
                is GuidedOutcome.Expired, is GuidedOutcome.NotFound, is GuidedOutcome.InvalidInput ->
                    return BindStage.Failed("binding refused: ${bindMessage(outcome)}")
                is GuidedOutcome.RateLimited, is GuidedOutcome.Failed, null -> {
                    if (attempt >= maxAttempts) {
                        val reason = (outcome as? GuidedOutcome.Failed)?.message ?: "transfer interrupted"
                        return BindStage.Failed("$reason after $attempt attempts")
                    }
                    sleeper(backoff(attempt))
                }
            }
        }
    }

    private sealed interface SidecarStage {
        data class Landed(val receiptStatus: String?) : SidecarStage
        data object Uncertain : SidecarStage
        data class AuthLost(val reason: String) : SidecarStage
        data class Failed(val reason: String) : SidecarStage
    }

    private suspend fun sidecarWithRetry(
        token: String,
        attemptId: String,
        operationId: String,
        expectedRevision: Int,
        sidecarJson: String,
    ): SidecarStage {
        var attempt = 0
        while (true) {
            attempt++
            val outcome = try {
                guided.uploadSidecar(token, attemptId, operationId, expectedRevision, sidecarJson)
            } catch (_: Exception) {
                null
            }
            when (outcome) {
                is GuidedOutcome.Success -> {
                    val delivery = outcome.value
                    if (delivery.state != "SPATIAL_PENDING" && delivery.receiptStatus == null) {
                        return SidecarStage.Failed("unexpected delivery state ${delivery.state}")
                    }
                    return SidecarStage.Landed(delivery.receiptStatus)
                }
                // Revision conflict: the bytes probably landed on an
                // earlier try. Assume SPATIAL_PENDING; the poll loop
                // reconciles via receipts. Never silently re-upload.
                is GuidedOutcome.Conflict -> return SidecarStage.Uncertain
                is GuidedOutcome.Unauthorized -> return SidecarStage.AuthLost("unauthorized: ${outcome.message}")
                is GuidedOutcome.Expired, is GuidedOutcome.NotFound, is GuidedOutcome.InvalidInput ->
                    return SidecarStage.Failed("sidecar refused: ${bindMessage(outcome)}")
                is GuidedOutcome.RateLimited, is GuidedOutcome.Failed, null -> {
                    if (attempt >= maxAttempts) {
                        val reason = (outcome as? GuidedOutcome.Failed)?.message ?: "transfer interrupted"
                        return SidecarStage.Failed("$reason after $attempt attempts")
                    }
                    sleeper(backoff(attempt))
                }
            }
        }
    }

    private suspend fun record(
        association: org.anastylos.capture.data.GuidedAssociation,
    ): org.anastylos.capture.data.GuidedAssociation {
        associations.recordGuidedAssociation(association)
        return association
    }

    private fun backoff(attempt: Int): Long = minOf(1_000L * (1L shl (attempt - 1)), 8_000L)

    private fun bindMessage(outcome: GuidedOutcome<*>): String = when (outcome) {
        is GuidedOutcome.Expired -> outcome.message
        is GuidedOutcome.NotFound -> outcome.message
        is GuidedOutcome.InvalidInput -> outcome.message
        else -> "refused"
    }
}
