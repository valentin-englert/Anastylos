package org.anastylos.capture.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.cameraDataStore: DataStore<Preferences> by preferencesDataStore(name = "camera")

/**
 * Operator-selected camera, shared by Live Observer and Guided
 * Capture. Stores the Camera2 camera ID verbatim; list/validation
 * against the live inventory happens at bind time, so a stale ID
 * (device change, OS update) resolves to the safe default with a
 * note instead of failing.
 */
interface CameraPrefs {
    /** Stored camera ID, or null when the operator never chose one. */
    val selectedCameraId: Flow<String?>
    suspend fun setSelectedCameraId(id: String)
}

class DataStoreCameraPrefs(context: Context) : CameraPrefs {
    private val store = context.applicationContext.cameraDataStore

    override val selectedCameraId: Flow<String?> =
        store.data.map { prefs -> prefs[SELECTED_CAMERA_ID] }

    override suspend fun setSelectedCameraId(id: String) {
        require(id.isNotBlank()) { "camera id must not be blank" }
        store.edit { prefs -> prefs[SELECTED_CAMERA_ID] = id }
    }

    companion object {
        private val SELECTED_CAMERA_ID = stringPreferencesKey("selected_camera_id")
    }
}
