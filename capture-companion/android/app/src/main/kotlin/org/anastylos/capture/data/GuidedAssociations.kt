package org.anastylos.capture.data

import java.time.Instant
import kotlinx.coroutines.flow.Flow
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.ReceiptKind

/**
 * Guided-control linkage for one captured image: which Core
 * session/request/attempt it belongs to, which package carried it,
 * and the latest delivery/receipt state observed. Unknown wire
 * spellings are refused at the repository boundary (fail closed),
 * never stored as silently-wrong states.
 */
data class GuidedAssociation(
    val imageId: String,
    val observationId: String,
    val packageId: String?,
    val guidedSessionId: String,
    val requestId: String?,
    val requestRevision: Int?,
    val attemptId: String?,
    val attemptNumber: Int?,
    val sidecarRevision: Int,
    val deliveryState: DeliveryState,
    val receiptStatus: ReceiptKind?,
    val updatedAt: Instant,
)

interface GuidedAssociationRepository {
    suspend fun recordGuidedAssociation(association: GuidedAssociation)
    suspend fun getGuidedAssociation(imageId: String): GuidedAssociation?
    fun observeGuidedAssociation(imageId: String): Flow<GuidedAssociation?>
}
