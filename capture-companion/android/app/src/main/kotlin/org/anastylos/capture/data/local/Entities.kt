package org.anastylos.capture.data.local

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import java.time.Instant

@Entity(tableName = "sessions")
data class SessionEntity(
    @PrimaryKey @ColumnInfo("session_id") val sessionId: String,
    @ColumnInfo("name") val name: String,
    @ColumnInfo("created_at") val createdAt: Instant,
    @ColumnInfo("updated_at") val updatedAt: Instant,
    @ColumnInfo("assembly_memory_session") val assemblyMemorySession: String?,
    @ColumnInfo("tray_default") val trayDefault: String?,
)

@Entity(
    tableName = "observations",
    foreignKeys = [
        ForeignKey(
            entity = SessionEntity::class,
            parentColumns = ["session_id"],
            childColumns = ["session_id"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index("session_id")],
)
data class ObservationEntity(
    @PrimaryKey @ColumnInfo("observation_id") val observationId: String,
    @ColumnInfo("session_id") val sessionId: String,
    @ColumnInfo("component_reference") val componentReference: String?,
    @ColumnInfo("tray_reference") val trayReference: String?,
    @ColumnInfo("captured_at") val capturedAt: Instant,
    @ColumnInfo("capture_source") val captureSource: String,
    @ColumnInfo("review_state") val reviewState: String,
    @ColumnInfo("operator_notes") val operatorNotes: String?,
    @ColumnInfo("markings_json") val markingsJson: String,
    @ColumnInfo("category_value") val categoryValue: String?,
    @ColumnInfo("category_state") val categoryState: String,
    @ColumnInfo("measurements_json") val measurementsJson: String,
    @ColumnInfo("quantity_value") val quantityValue: Long?,
    @ColumnInfo("quantity_state") val quantityState: String,
    @ColumnInfo("candidates_json") val candidatesJson: String,
    @ColumnInfo("app_version") val appVersion: String,
    @ColumnInfo("user_agent") val userAgent: String?,
    @ColumnInfo("export_history_json") val exportHistoryJson: String,
)

@Entity(
    tableName = "images",
    foreignKeys = [
        ForeignKey(
            entity = ObservationEntity::class,
            parentColumns = ["observation_id"],
            childColumns = ["observation_id"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index("observation_id")],
)
data class ImageEntity(
    @PrimaryKey @ColumnInfo("image_id") val imageId: String,
    @ColumnInfo("observation_id") val observationId: String,
    @ColumnInfo("role") val role: String,
    @ColumnInfo("filename") val filename: String,
    @ColumnInfo("mime") val mime: String,
    @ColumnInfo("byte_length") val byteLength: Long,
    @ColumnInfo("sha256") val sha256: String,
    @ColumnInfo("captured_at") val capturedAt: Instant,
    @ColumnInfo("crop_json") val cropJson: String?,
)
