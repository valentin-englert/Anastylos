package org.anastylos.capture.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.doublePreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.guidedDataStore: DataStore<Preferences> by preferencesDataStore(name = "guided")

/**
 * Guided-capture operator settings. The sharpness reference is set by
 * an explicit operator action ("calibrate sharpness" on a visibly
 * sharp, stable frame) and persists per device; until set, image
 * quality stays Unknown and automatic capture stays blocked.
 */
interface GuidedPrefs {
    val sharpnessReference: Flow<Double?>
    suspend fun setSharpnessReference(value: Double)
    suspend fun clearSharpnessReference()
    /** Last-seen Core `change_seq` cursor per guided session ("0" = start). */
    fun changeCursor(sessionId: String): Flow<String>
    suspend fun setChangeCursor(sessionId: String, cursor: String)
    /**
     * Operator-declared mapping from a Core board uuid to the
     * physical frame the operator aimed at (surface_a3, ...).
     */
    fun boardFrameFor(boardUuid: String): Flow<String?>
    suspend fun setBoardFrame(boardUuid: String, frameId: String)
}

class DataStoreGuidedPrefs(context: Context) : GuidedPrefs {
    private val store = context.applicationContext.guidedDataStore

    override val sharpnessReference: Flow<Double?> =
        store.data.map { prefs -> prefs[SHARPNESS_REFERENCE] }

    override suspend fun setSharpnessReference(value: Double) {
        require(value.isFinite() && value > 0.0) { "sharpness reference must be positive" }
        store.edit { prefs -> prefs[SHARPNESS_REFERENCE] = value }
    }

    override suspend fun clearSharpnessReference() {
        store.edit { prefs -> prefs.remove(SHARPNESS_REFERENCE) }
    }

    override fun changeCursor(sessionId: String): Flow<String> =
        store.data.map { prefs -> prefs[stringPreferencesKey(cursorKey(sessionId))] ?: "0" }

    override suspend fun setChangeCursor(sessionId: String, cursor: String) {
        require(cursor.isNotBlank()) { "cursor must not be blank" }
        store.edit { prefs -> prefs[stringPreferencesKey(cursorKey(sessionId))] = cursor }
    }

    override fun boardFrameFor(boardUuid: String): Flow<String?> =
        store.data.map { prefs -> prefs[stringPreferencesKey(boardKey(boardUuid))] }

    override suspend fun setBoardFrame(boardUuid: String, frameId: String) {
        require(boardUuid.isNotBlank() && frameId.isNotBlank()) { "board mapping must not be blank" }
        store.edit { prefs -> prefs[stringPreferencesKey(boardKey(boardUuid))] = frameId }
    }

    companion object {
        private val SHARPNESS_REFERENCE = doublePreferencesKey("sharpness_reference")
        private fun cursorKey(sessionId: String) = "cursor_$sessionId"
        private fun boardKey(boardUuid: String) = "board_frame_$boardUuid"
    }
}
