package org.anastylos.capture.data.local

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert
import java.time.Instant
import kotlinx.coroutines.flow.Flow

/**
 * Guided-control linkage for one captured image (Room v2, additive).
 *
 * One row per guided image: which Core session/request/attempt it
 * belongs to, which package carried it, and the latest delivery /
 * receipt state the phone has observed. Local evidence rows are
 * untouched — this table only links outward. Deleting the image row
 * cascades the link; deleting the link never touches evidence.
 */
@Entity(
    tableName = "guided_associations",
    foreignKeys = [
        ForeignKey(
            entity = ImageEntity::class,
            parentColumns = ["image_id"],
            childColumns = ["image_id"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index("attempt_id")],
)
data class GuidedAssociationEntity(
    @PrimaryKey @ColumnInfo("image_id") val imageId: String,
    @ColumnInfo("observation_id") val observationId: String,
    @ColumnInfo("package_id") val packageId: String?,
    @ColumnInfo("guided_session_id") val guidedSessionId: String,
    @ColumnInfo("request_id") val requestId: String?,
    @ColumnInfo("request_revision") val requestRevision: Int?,
    @ColumnInfo("attempt_id") val attemptId: String?,
    @ColumnInfo("attempt_number") val attemptNumber: Int?,
    @ColumnInfo("sidecar_revision") val sidecarRevision: Int,
    @ColumnInfo("delivery_state") val deliveryState: String,
    @ColumnInfo("receipt_status") val receiptStatus: String?,
    @ColumnInfo("updated_at") val updatedAt: Instant,
)

@Dao
interface GuidedAssociationDao {
    @Query("SELECT * FROM guided_associations WHERE image_id = :imageId")
    suspend fun getByImageId(imageId: String): GuidedAssociationEntity?

    @Query("SELECT * FROM guided_associations WHERE image_id = :imageId")
    fun observeByImageId(imageId: String): Flow<GuidedAssociationEntity?>

    @Query("SELECT * FROM guided_associations WHERE attempt_id = :attemptId")
    suspend fun getByAttemptId(attemptId: String): GuidedAssociationEntity?

    @Upsert
    suspend fun upsert(association: GuidedAssociationEntity)

    @Query("DELETE FROM guided_associations WHERE image_id = :imageId")
    suspend fun deleteByImageId(imageId: String)
}
