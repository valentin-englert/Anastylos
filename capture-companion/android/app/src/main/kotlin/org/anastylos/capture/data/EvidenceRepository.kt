package org.anastylos.capture.data

import java.time.Instant
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.serialization.builtins.ListSerializer
import org.anastylos.capture.data.local.CandidateDto
import org.anastylos.capture.data.local.CaptureDatabase
import org.anastylos.capture.data.local.CropDto
import org.anastylos.capture.data.local.EvidenceJson
import org.anastylos.capture.data.local.ExportRecordDto
import org.anastylos.capture.data.local.GuidedAssociationEntity
import org.anastylos.capture.data.local.ImageEntity
import org.anastylos.capture.data.local.MarkingDto
import org.anastylos.capture.data.local.MeasurementDto
import org.anastylos.capture.data.local.ObservationEntity
import org.anastylos.capture.data.local.SessionEntity
import org.anastylos.capture.data.local.captureSourceFromWire
import org.anastylos.capture.data.local.epistemicFromWire
import org.anastylos.capture.data.local.imageRoleFromWire
import org.anastylos.capture.data.local.instantFromWire
import org.anastylos.capture.data.local.instantToWire
import org.anastylos.capture.data.local.reviewStateFromWire
import org.anastylos.capture.data.local.toDomain
import org.anastylos.capture.data.local.toDto
import org.anastylos.capture.data.local.toWire
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.ConfirmedField
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.ExportRecord
import org.anastylos.capture.domain.ImageRole
import org.anastylos.capture.domain.ReviewState
import org.anastylos.capture.domain.Transport
import org.anastylos.capture.domain.newId
import org.anastylos.capture.domain.nowUtc
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.ReceiptKind
import org.anastylos.capture.domain.validateImage
import org.anastylos.capture.domain.validateObservationDraft
import org.anastylos.capture.domain.validateSessionDraft

interface EvidenceRepository {
    fun observeSessions(): Flow<List<CaptureSession>>
    fun observeSession(id: String): Flow<CaptureSession?>
    suspend fun getSession(id: String): CaptureSession?
    suspend fun createSession(name: String): CaptureSession
    /** The session with [id], created under [name] when absent (a guided session's local twin). */
    suspend fun ensureSession(id: String, name: String): CaptureSession
    suspend fun renameSession(id: String, name: String)
    suspend fun deleteSession(id: String)

    fun observeObservations(sessionId: String): Flow<List<ComponentObservation>>
    fun observeObservation(id: String): Flow<ComponentObservation?>
    suspend fun getObservation(id: String): ComponentObservation?
    suspend fun getAllObservations(): List<ComponentObservation>
    suspend fun createObservation(sessionId: String, source: CaptureSource): ComponentObservation
    suspend fun saveObservation(obs: ComponentObservation)
    suspend fun deleteObservation(id: String)

    suspend fun recordImage(image: EvidenceImage)
    suspend fun markSynced(observationIds: List<String>, packageId: String, exportedAt: Instant)
}

/**
 * Room-backed repository. The database is the source of truth; every write
 * is validated first, and row deletes always precede file deletes so a
 * crash can only orphan bytes, never corrupt records.
 */
class RoomEvidenceRepository(
    private val db: CaptureDatabase,
    private val files: EvidenceFileStorePort,
    private val clock: () -> Instant = ::nowUtc,
    private val appVersion: String,
) : EvidenceRepository, GuidedAssociationRepository {

    override fun observeSessions(): Flow<List<CaptureSession>> =
        db.sessions().observeAll().map { rows -> rows.map { it.toDomain() } }

    override fun observeSession(id: String): Flow<CaptureSession?> =
        db.sessions().observeById(id).map { it?.toDomain() }

    override suspend fun getSession(id: String): CaptureSession? =
        db.sessions().getById(id)?.toDomain()

    override suspend fun ensureSession(id: String, name: String): CaptureSession {
        getSession(id)?.let { return it }
        val now = clock()
        val session = CaptureSession(id, name.trim(), now, now, null, null)
        val issues = validateSessionDraft(session)
        require(issues.isEmpty()) { issues.joinToString("; ") { "${it.path}: ${it.message}" } }
        db.sessions().upsert(session.toEntity())
        return session
    }

    override suspend fun createSession(name: String): CaptureSession {
        val now = clock()
        val session = CaptureSession(newId(), name.trim(), now, now, null, null)
        val issues = validateSessionDraft(session)
        require(issues.isEmpty()) { issues.joinToString("; ") { "${it.path}: ${it.message}" } }
        db.sessions().upsert(session.toEntity())
        return session
    }

    override suspend fun renameSession(id: String, name: String) {
        val current = db.sessions().getById(id) ?: return
        val renamed = current.copy(name = name.trim(), updatedAt = clock())
        val issues = validateSessionDraft(renamed.toDomain())
        require(issues.isEmpty()) { issues.joinToString("; ") { "${it.path}: ${it.message}" } }
        db.sessions().upsert(renamed)
    }

    override suspend fun deleteSession(id: String) {
        val observations = db.observations().getAll().filter { it.sessionId == id }
        db.sessions().deleteById(id)
        for (obs in observations) files.deleteObservationFiles(obs.observationId)
    }

    override fun observeObservations(sessionId: String): Flow<List<ComponentObservation>> =
        db.observations().observeBySession(sessionId).flatMapLatest { rows ->
            if (rows.isEmpty()) return@flatMapLatest flowOf(emptyList())
            combine(rows.map { row -> db.images().observeByObservation(row.observationId) }) { imageRows ->
                val byObservation = imageRows.flatMap { it.toList() }.groupBy { it.observationId }
                rows.map { row -> row.toDomain(byObservation[row.observationId].orEmpty()) }
            }
        }

    override fun observeObservation(id: String): Flow<ComponentObservation?> =
        db.observations().observeById(id).flatMapLatest { row ->
            if (row == null) return@flatMapLatest flowOf(null)
            db.images().observeByObservation(id).map { images -> row.toDomain(images) }
        }

    override suspend fun getObservation(id: String): ComponentObservation? {
        val row = db.observations().getById(id) ?: return null
        return row.toDomain(db.images().getByObservation(id))
    }

    override suspend fun getAllObservations(): List<ComponentObservation> {
        return db.observations().getAll().map { row ->
            row.toDomain(db.images().getByObservation(row.observationId))
        }
    }

    override suspend fun createObservation(sessionId: String, source: CaptureSource): ComponentObservation {
        require(db.sessions().getById(sessionId) != null) { "unknown session: $sessionId" }
        val obs = ComponentObservation(
            observationId = newId(),
            sessionId = sessionId,
            componentReference = null,
            trayReference = null,
            capturedAt = clock(),
            captureSource = source,
            reviewState = ReviewState.UNREVIEWED,
            operatorNotes = null,
            markings = emptyList(),
            category = ConfirmedField(null, EpistemicState.UNKNOWN),
            measurements = emptyList(),
            quantity = ConfirmedField(null, EpistemicState.UNKNOWN),
            candidates = emptyList(),
            appVersion = appVersion,
            userAgent = null,
            exportHistory = emptyList(),
            images = emptyList(),
        )
        db.observations().upsert(obs.toEntity())
        touchSession(sessionId)
        return obs
    }

    override suspend fun saveObservation(obs: ComponentObservation) {
        val issues = validateObservationDraft(obs)
        require(issues.isEmpty()) { issues.joinToString("; ") { "${it.path}: ${it.message}" } }
        db.observations().upsert(obs.toEntity())
        touchSession(obs.sessionId)
    }

    override suspend fun deleteObservation(id: String) {
        db.observations().deleteById(id)
        files.deleteObservationFiles(id)
    }

    override suspend fun recordImage(image: EvidenceImage) {
        val issues = validateImage(image)
        require(issues.isEmpty()) { issues.joinToString("; ") { "${it.path}: ${it.message}" } }
        require(db.observations().getById(image.observationId) != null) {
            "unknown observation: ${image.observationId}"
        }
        db.images().upsert(image.toEntity())
        val obs = db.observations().getById(image.observationId)
        if (obs != null) touchSession(obs.sessionId)
    }

    override suspend fun markSynced(observationIds: List<String>, packageId: String, exportedAt: Instant) {
        for (id in observationIds) {
            val row = db.observations().getById(id) ?: continue
            val history = decodeHistory(row.exportHistoryJson)
            if (history.none { it.packageId == packageId }) {
                val updated = history + ExportRecord(packageId, exportedAt, Transport.SYNC)
                db.observations().upsert(row.copy(exportHistoryJson = encodeHistory(updated)))
            }
        }
    }

    override suspend fun recordGuidedAssociation(association: GuidedAssociation) {
        require(association.sidecarRevision >= 0) { "sidecarRevision must be >= 0" }
        if (association.requestRevision != null) {
            require(association.requestRevision >= 1) { "requestRevision must be >= 1" }
        }
        if (association.attemptNumber != null) {
            require(association.attemptNumber >= 1) { "attemptNumber must be >= 1" }
        }
        db.guidedAssociations().upsert(association.toEntity())
    }

    override suspend fun getGuidedAssociation(imageId: String): GuidedAssociation? =
        db.guidedAssociations().getByImageId(imageId)?.toDomain()

    override fun observeGuidedAssociation(imageId: String): Flow<GuidedAssociation?> =
        db.guidedAssociations().observeByImageId(imageId).map { it?.toDomain() }

    private suspend fun touchSession(sessionId: String) {
        val current = db.sessions().getById(sessionId) ?: return
        db.sessions().upsert(current.copy(updatedAt = clock()))
    }
}

/** Narrow file capability the repository needs; keeps java.io out of tests. */
interface EvidenceFileStorePort {
    fun deleteObservationFiles(observationId: String)
}

// -- mapping (entities hold wire strings; vocabulary lives in Serialized.kt) --

private fun GuidedAssociation.toEntity() = GuidedAssociationEntity(
    imageId = imageId,
    observationId = observationId,
    packageId = packageId,
    guidedSessionId = guidedSessionId,
    requestId = requestId,
    requestRevision = requestRevision,
    attemptId = attemptId,
    attemptNumber = attemptNumber,
    sidecarRevision = sidecarRevision,
    deliveryState = deliveryState.name,
    receiptStatus = receiptStatus?.wire,
    updatedAt = updatedAt,
)

private fun GuidedAssociationEntity.toDomain(): GuidedAssociation {
    val delivery = try {
        DeliveryState.valueOf(deliveryState)
    } catch (_: IllegalArgumentException) {
        throw IllegalStateException("unknown delivery_state in store: $deliveryState")
    }
    val receipt = receiptStatus?.let { wire ->
        ReceiptKind.entries.firstOrNull { it.wire == wire }
            ?: throw IllegalStateException("unknown receipt_status in store: $wire")
    }
    return GuidedAssociation(
        imageId = imageId,
        observationId = observationId,
        packageId = packageId,
        guidedSessionId = guidedSessionId,
        requestId = requestId,
        requestRevision = requestRevision,
        attemptId = attemptId,
        attemptNumber = attemptNumber,
        sidecarRevision = sidecarRevision,
        deliveryState = delivery,
        receiptStatus = receipt,
        updatedAt = updatedAt,
    )
}

private fun SessionEntity.toDomain() = CaptureSession(
    sessionId, name, createdAt, updatedAt, assemblyMemorySession, trayDefault,
)

private fun CaptureSession.toEntity() = SessionEntity(
    sessionId, name, createdAt, updatedAt, assemblyMemorySession, trayDefault,
)

private fun decodeHistory(json: String): List<ExportRecord> =
    EvidenceJson.decodeFromString(ListSerializer(ExportRecordDto.serializer()), json).map { it.toDomain() }

private fun encodeHistory(history: List<ExportRecord>): String =
    EvidenceJson.encodeToString(
        ListSerializer(ExportRecordDto.serializer()),
        history.map { it.toDto() },
    )

private fun ObservationEntity.toDomain(images: List<ImageEntity>) = ComponentObservation(
    observationId = observationId,
    sessionId = sessionId,
    componentReference = componentReference,
    trayReference = trayReference,
    capturedAt = capturedAt,
    captureSource = captureSourceFromWire(captureSource),
    reviewState = reviewStateFromWire(reviewState),
    operatorNotes = operatorNotes,
    markings = EvidenceJson.decodeFromString(ListSerializer(MarkingDto.serializer()), markingsJson)
        .map { it.toDomain() },
    category = ConfirmedField(categoryValue, epistemicFromWire(categoryState)),
    measurements = EvidenceJson.decodeFromString(ListSerializer(MeasurementDto.serializer()), measurementsJson)
        .map { it.toDomain() },
    quantity = ConfirmedField(quantityValue, epistemicFromWire(quantityState)),
    candidates = EvidenceJson.decodeFromString(ListSerializer(CandidateDto.serializer()), candidatesJson)
        .map { it.toDomain() },
    appVersion = appVersion,
    userAgent = userAgent,
    exportHistory = decodeHistory(exportHistoryJson),
    images = images.map { it.toDomain() },
)

private fun ComponentObservation.toEntity() = ObservationEntity(
    observationId = observationId,
    sessionId = sessionId,
    componentReference = componentReference,
    trayReference = trayReference,
    capturedAt = capturedAt,
    captureSource = captureSource.toWire(),
    reviewState = reviewState.toWire(),
    operatorNotes = operatorNotes,
    markingsJson = EvidenceJson.encodeToString(
        ListSerializer(MarkingDto.serializer()), markings.map { it.toDto() },
    ),
    categoryValue = category.value,
    categoryState = category.state.toWire(),
    measurementsJson = EvidenceJson.encodeToString(
        ListSerializer(MeasurementDto.serializer()), measurements.map { it.toDto() },
    ),
    quantityValue = quantity.value,
    quantityState = quantity.state.toWire(),
    candidatesJson = EvidenceJson.encodeToString(
        ListSerializer(CandidateDto.serializer()), candidates.map { it.toDto() },
    ),
    appVersion = appVersion,
    userAgent = userAgent,
    exportHistoryJson = encodeHistory(exportHistory),
)

private fun ImageEntity.toDomain() = EvidenceImage(
    imageId, observationId, imageRoleFromWire(role), filename, mime,
    byteLength, sha256, capturedAt,
    cropJson?.let { EvidenceJson.decodeFromString(CropDto.serializer(), it).toDomain() },
)

private fun EvidenceImage.toEntity() = ImageEntity(
    imageId, observationId, role.toWire(), filename, mime, byteLength, sha256, capturedAt,
    crop?.let { EvidenceJson.encodeToString(CropDto.serializer(), it.toDto()) },
)
