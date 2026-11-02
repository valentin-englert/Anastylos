package org.anastylos.capture.ui.onboarding

import android.content.Context

/** Whether the first-run guide opens at launch; on until the user opts out. */
class OnboardingPrefs(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var showAtLaunch: Boolean
        get() = prefs.getBoolean(KEY_SHOW, true)
        set(value) = prefs.edit().putBoolean(KEY_SHOW, value).apply()

    private companion object {
        const val FILE = "ui_prefs"
        const val KEY_SHOW = "show_onboarding"
    }
}
