package org.anastylos.capture.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import org.anastylos.capture.camera.CameraSettings
import org.anastylos.capture.camera.DEFAULT_PROFILE
import org.anastylos.capture.camera.SETTING_PROFILES
import org.anastylos.capture.camera.parseSettings
import org.anastylos.capture.camera.serializeSettings

private val Context.cameraSettingsDataStore: DataStore<Preferences> by preferencesDataStore(
    name = "camera_settings",
)

/**
 * Manual-control settings keyed by (camera ID, named profile),
 * shared by Live Observer and Guided Capture. Malformed stored
 * values decode to null and resolve to [CameraSettings] defaults;
 * unknown profile names resolve to [DEFAULT_PROFILE].
 */
interface CameraSettingsStore {
    fun settings(cameraId: String, profile: String): Flow<CameraSettings>
    suspend fun update(cameraId: String, profile: String, value: CameraSettings)
    val activeProfile: Flow<String>
    suspend fun setActiveProfile(profile: String)
}

class DataStoreCameraSettingsStore(context: Context) : CameraSettingsStore {
    private val store = context.applicationContext.cameraSettingsDataStore

    override fun settings(cameraId: String, profile: String): Flow<CameraSettings> {
        val key = stringPreferencesKey(keyFor(cameraId, profile))
        return store.data.map { prefs -> parseSettings(prefs[key]) ?: CameraSettings() }
    }

    override suspend fun update(cameraId: String, profile: String, value: CameraSettings) {
        val key = stringPreferencesKey(keyFor(cameraId, profile))
        store.edit { prefs -> prefs[key] = serializeSettings(value) }
    }

    override val activeProfile: Flow<String> =
        store.data.map { prefs ->
            val name = prefs[PROFILE_KEY]
            if (name in SETTING_PROFILES) name!! else DEFAULT_PROFILE
        }

    override suspend fun setActiveProfile(profile: String) {
        val name = if (profile in SETTING_PROFILES) profile else DEFAULT_PROFILE
        store.edit { prefs -> prefs[PROFILE_KEY] = name }
    }

    private fun keyFor(cameraId: String, profile: String): String {
        val safeProfile = if (profile in SETTING_PROFILES) profile else DEFAULT_PROFILE
        return "camset_${cameraId}_$safeProfile"
    }

    companion object {
        private val PROFILE_KEY = stringPreferencesKey("active_profile")
    }
}
