package org.anastylos.capture.ui.guided

import org.anastylos.capture.guidance.DwellState
import org.anastylos.capture.guidance.RegistrationState
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.guidance.SessionState
import org.anastylos.capture.voice.VoiceController

/** Semantic tone of the status banner; the screen maps it to colours. */
enum class BannerTone { NEUTRAL, WAITING, ACTION, SUCCESS, PROBLEM }

/** One current task in words readable at arm's length. */
data class StatusBanner(val title: String, val detail: String?, val tone: BannerTone)

/** What the operator should do or expect right now; null detail means nothing to add. */
fun statusBannerOf(state: GuidanceUiState): StatusBanner {
    if (!state.remoteAttached) {
        return StatusBanner("No session yet", "Enter the session id shown on the workstation", BannerTone.PROBLEM)
    }
    val card = requestCardOf(state)
    if (card != null) {
        val outcome = card.outcome
        return when {
            outcome != null && outcome.retake -> StatusBanner(outcome.text, card.label, BannerTone.PROBLEM)
            outcome != null && outcome.text == "Photo sent" -> StatusBanner("Photo sent", outcome.note ?: card.label, BannerTone.SUCCESS)
            outcome != null -> StatusBanner(outcome.text, card.label, BannerTone.WAITING)
            isHoldStill(state) -> StatusBanner("Hold still", card.label, BannerTone.ACTION)
            else -> StatusBanner(card.hint, card.label, BannerTone.ACTION)
        }
    }
    return when (state.session) {
        SessionState.ACTIVE -> StatusBanner("Waiting for a request", trackingDetail(state), BannerTone.WAITING)
        SessionState.PAUSED -> StatusBanner("Paused", "Resume to receive requests", BannerTone.NEUTRAL)
        SessionState.DISARMED -> StatusBanner("Connected", "Press “Start photo requests” to receive them", BannerTone.NEUTRAL)
        else -> StatusBanner("Session ended", "Start a new session", BannerTone.NEUTRAL)
    }
}

private fun isHoldStill(state: GuidanceUiState): Boolean =
    state.request == RequestState.HOLD_STILL ||
        (state.request == RequestState.CAPTURING) ||
        state.dwell is DwellState.Ready

private fun trackingDetail(state: GuidanceUiState): String = when (state.registration) {
    RegistrationState.ACQUIRED -> "Board tracked"
    RegistrationState.DEGRADED -> "Show more of the board"
    RegistrationState.LOST, RegistrationState.UNREGISTERED -> "Point the camera at the board"
    RegistrationState.BAD_CALIBRATION -> "Calibration not valid"
}

/** Voice link in one word; null when voice is off. */
fun voiceLabelOf(voice: VoiceController.State): String? = when {
    voice.speaking -> "Speaking"
    voice.busy -> "Thinking"
    voice.listening -> "Listening"
    else -> null
}
