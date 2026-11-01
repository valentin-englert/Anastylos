package org.anastylos.capture.ui.guided

import org.anastylos.capture.guidance.CueAxis
import org.anastylos.capture.guidance.DwellState
import org.anastylos.capture.guidance.ImageQualityVerdict
import org.anastylos.capture.guidance.MovementCue
import org.anastylos.capture.guidance.ReceiptKind
import org.anastylos.capture.guidance.RegistrationState
import org.anastylos.capture.guidance.RequestState

/**
 * The workstation's view request as the agent's question on screen:
 * the request label verbatim, a live hint from the existing guidance,
 * and the outcome. Text only restates phone state; a captured image
 * says nothing about part identity.
 */
data class RequestCard(
    val label: String,
    val hint: String,
    val outcome: RequestOutcome?,
)

/** [note] is an optional second line, e.g. a low-quality hint on a sent photo. */
data class RequestOutcome(val text: String, val retake: Boolean, val note: String? = null)

/** Null without an adopted workstation request. */
fun requestCardOf(state: GuidanceUiState): RequestCard? {
    val request = state.remoteRequest ?: return null
    return RequestCard(
        label = request.targetLabel.ifBlank { "View requested" },
        hint = liveHint(state),
        outcome = outcomeOf(state),
    )
}

internal fun liveHint(state: GuidanceUiState): String = when {
    state.request == RequestState.CAPTURING || state.request == RequestState.VERIFYING_IMAGE -> "Capturing…"
    state.request == RequestState.CAPTURED_LOCAL -> "Captured."
    state.request == RequestState.HOLD_STILL && state.dwell is DwellState.Ready -> "Hold still — capturing…"
    state.request == RequestState.HOLD_STILL && !state.attemptReady -> "Hold still — waiting for the workstation…"
    state.request == RequestState.HOLD_STILL -> "Hold still"
    state.registration == RegistrationState.BAD_CALIBRATION -> "Calibration not valid — capture is blocked"
    state.registration == RegistrationState.LOST ||
        state.registration == RegistrationState.UNREGISTERED -> "Point the camera at the A3 board"
    state.registration == RegistrationState.DEGRADED -> "Show more of the board"
    state.cues.isNotEmpty() -> cueText(state.cues.first())
    // On target but an image gate blocks the shutter: say which, never "hold still".
    qualityBlock(state) != null -> qualityBlock(state)!!
    else -> "On target — hold still"
}

/** The blocking image-quality reason in operator words; motion only warns. */
private fun qualityBlock(state: GuidanceUiState): String? {
    val reasons = (state.qualityVerdict as? ImageQualityVerdict.Rejected)?.reasons ?: return null
    return when {
        "sharpness" in reasons -> "Image not sharp — check focus and light"
        "dark" in reasons -> "Too dark — add light"
        "saturated" in reasons -> "Too bright — reduce glare"
        else -> null
    }
}

private fun outcomeOf(state: GuidanceUiState): RequestOutcome? {
    when (state.request) {
        RequestState.RETAKE_REQUIRED -> return RequestOutcome("Retake needed: image check failed", retake = true)
        RequestState.UNREACHABLE -> return RequestOutcome("Marked as can't reach", retake = true)
        RequestState.CANCELLED -> return RequestOutcome("Cancelled by the workstation", retake = false)
        RequestState.EXPIRED -> return RequestOutcome("Request expired", retake = false)
        RequestState.SUPERSEDED -> return RequestOutcome("Replaced by a newer request", retake = false)
        else -> Unit
    }
    when (state.delivery?.receiptStatus) {
        ReceiptKind.QUALIFIED, ReceiptKind.PACKAGE_ONLY -> return RequestOutcome(
            "Photo sent", retake = false,
            note = if (state.captureResult?.qualityLow == true) LOW_QUALITY_NOTE else null,
        )
        ReceiptKind.REJECTED -> return RequestOutcome("Retake needed: workstation rejected the image", retake = true)
        null -> Unit
        else -> return RequestOutcome("Sent too late for this request", retake = true)
    }
    val note = state.deliveryNote
    if (note != null && FAILURE_WORDS.any { note.contains(it) }) {
        return RequestOutcome("Retake needed: $note", retake = true)
    }
    if (state.captureResult != null || state.delivery != null) return RequestOutcome("Captured — sending…", retake = false)
    return null
}

private const val LOW_QUALITY_NOTE = "a bit blurry — retake if the drawing looks wrong"

private val FAILURE_WORDS = listOf("rejected", "failed", "blocked", "lost", "deferred")

internal fun cueText(cue: MovementCue): String {
    val magnitude = "%.0f".format(kotlin.math.abs(cue.magnitude))
    return when (cue.axis) {
        CueAxis.LATERAL -> if (cue.magnitude > 0) "→ Move right ${magnitude}mm" else "← Move left ${magnitude}mm"
        CueAxis.VERTICAL -> if (cue.magnitude > 0) "↓ Move down ${magnitude}mm" else "↑ Move up ${magnitude}mm"
        CueAxis.DEPTH -> if (cue.magnitude > 0) "⊕ Move back ${magnitude}mm" else "⊙ Move closer ${magnitude}mm"
        CueAxis.YAW -> if (cue.magnitude > 0) "↻ Turn right ${magnitude}°" else "↺ Turn left ${magnitude}°"
        CueAxis.PITCH -> if (cue.magnitude > 0) "↓ Tilt down ${magnitude}°" else "↑ Tilt up ${magnitude}°"
        CueAxis.ROLL -> if (cue.magnitude > 0) "↻ Roll right ${magnitude}°" else "↺ Roll left ${magnitude}°"
    }
}
