package org.anastylos.capture.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.samplingDataStore: DataStore<Preferences> by preferencesDataStore(name = "sampling")

/** Ordinary sampling preferences. No secrets; safe to back up. */
interface SamplingPrefs {
    val intervalSeconds: Flow<Int>
    suspend fun setIntervalSeconds(seconds: Int)
}

class DataStoreSamplingPrefs(context: Context) : SamplingPrefs {
    private val store = context.applicationContext.samplingDataStore

    override val intervalSeconds: Flow<Int> =
        store.data.map { it[KEY_INTERVAL] ?: DEFAULT_INTERVAL_SECONDS }

    override suspend fun setIntervalSeconds(seconds: Int) {
        require(seconds in 1..300) { "sampling interval must be 1..300 seconds" }
        store.edit { it[KEY_INTERVAL] = seconds }
    }

    companion object {
        const val DEFAULT_INTERVAL_SECONDS = 5
        private val KEY_INTERVAL = intPreferencesKey("interval_seconds")
    }
}
