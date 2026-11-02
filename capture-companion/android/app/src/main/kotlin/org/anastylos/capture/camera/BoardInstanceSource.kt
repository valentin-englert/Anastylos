package org.anastylos.capture.camera

import android.content.Context
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * A measured print of a board sheet as evidence cites it: the record's
 * uuid and the sha256 of its exact bytes (the workstation stages the same
 * bytes under that hash).
 */
data class BoardInstanceReference(val boardInstanceId: String, val sha256: String)

/**
 * Measured board-instance records (`anastylos-board-instance@1`). Returns a
 * reference only for a record of [boardFrameId] whose layout hash equals the
 * layout the phone solves against; anything else stays nominal.
 * Implementations must never throw.
 */
interface BoardInstanceSource {
    fun forBoard(boardFrameId: String, layoutSha256: String): BoardInstanceReference?
}

/** Reads `board-instance-<frame>.json` from APK assets. */
class AssetBoardInstanceSource(context: Context) : BoardInstanceSource {
    private val appContext = context.applicationContext
    private val json = Json { ignoreUnknownKeys = true }

    @Serializable
    private data class Bars(
        @SerialName("horizontal_mm") val horizontalMm: Double? = null,
        @SerialName("vertical_mm") val verticalMm: Double? = null,
    )

    @Serializable
    private data class Record(
        val schema: String = "",
        @SerialName("board_instance_id") val boardInstanceId: String = "",
        @SerialName("layout_sha256") val layoutSha256: String = "",
        @SerialName("board_frame_id") val boardFrameId: String = "",
        @SerialName("measured_bars_mm") val measuredBarsMm: Bars? = null,
    )

    override fun forBoard(boardFrameId: String, layoutSha256: String): BoardInstanceReference? = try {
        val bytes = appContext.assets.open("board-instance-$boardFrameId.json").use { it.readBytes() }
        val record = json.decodeFromString(Record.serializer(), bytes.toString(Charsets.UTF_8))
        val measured = record.measuredBarsMm?.let { it.horizontalMm != null && it.verticalMm != null } == true
        if (record.schema == SCHEMA && record.boardFrameId == boardFrameId &&
            record.layoutSha256 == layoutSha256 && measured && record.boardInstanceId.isNotBlank()
        ) {
            val sha = java.security.MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it) }
            BoardInstanceReference(record.boardInstanceId, sha)
        } else {
            null
        }
    } catch (_: Exception) {
        null
    }

    private companion object {
        const val SCHEMA = "anastylos-board-instance@1"
    }
}
