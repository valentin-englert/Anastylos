package org.anastylos.capture.video

/** The one-take protocol: an empty sheet first (the lock is taken on it), then the object is placed. */
const val EMPTY_SHEET_MS = 5_000L

/** How long "Now place the object" stays on screen before the screen shows the plain recording. */
const val PLACE_OBJECT_MS = 4_000L

sealed interface RecordingPhase {
    /** Keep the sheet empty; [secondsLeft] counts down to the object. */
    data class EmptySheet(val secondsLeft: Int) : RecordingPhase

    data object PlaceObject : RecordingPhase

    data object Normal : RecordingPhase
}

fun recordingPhase(elapsedMs: Long, emptySheetMs: Long = EMPTY_SHEET_MS): RecordingPhase = when {
    elapsedMs < emptySheetMs -> RecordingPhase.EmptySheet(((emptySheetMs - elapsedMs + 999) / 1000).toInt())
    elapsedMs < emptySheetMs + PLACE_OBJECT_MS -> RecordingPhase.PlaceObject
    else -> RecordingPhase.Normal
}
