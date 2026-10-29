package org.anastylos.capture.guidance

/**
 * Automatic-shutter safety kernel (pure). Decides whether the caller
 * may dispatch [RequestEvent.DwellReady] for a HOLD_STILL request.
 * Every §7 precondition is an explicit input; the first violation
 * wins and names its reason code for the UI and audit log.
 *
 * The kernel is necessary but not sufficient: the request reducer
 * owns the HOLD_STILL→CAPTURING edge and its exactly-once
 * `shutterCommitted` flag, the session reducer owns arming, and the
 * caller persists the arming token so [alreadyFiredForArming] stays
 * true across callback retries and process restart.
 */
object ShutterGate {
    data class Input(
        /** Session reducer state (explicit local arming). */
        val session: SessionState,
        /** Request reducer state. */
        val request: RequestState,
        /** Sticky late mark, if any. */
        val lateMark: LateMark?,
        /** Phone registration (mapped from the estimator wire status). */
        val registration: RegistrationState,
        /** Freshest pose age in ms, or null when no pose exists. */
        val poseAgeMs: Long?,
        /** Dwell accumulator state. */
        val dwell: DwellState,
        /** Ellipsoid orientation gate (coarse + fine). */
        val orientationOk: Boolean,
        /** Ellipsoid distance gate. */
        val distanceOk: Boolean,
        /** Image-quality verdict for the current stream. */
        val qualityOk: Boolean,
        /** Board ID the estimator is solving against. */
        val boardId: String?,
        /** Board ID the request targets. */
        val targetBoardId: String?,
        /** Intrinsics profile ID in use. */
        val profileId: String?,
        /** Profile ID the request requires (null = any factory ok). */
        val requiredProfileId: String?,
        /** Consent grant still live and unexpired. */
        val grantLive: Boolean,
        /** App in the foreground with camera permission. */
        val foreground: Boolean,
        /** A shutter already fired for this arming token. */
        val alreadyFiredForArming: Boolean,
    )

    sealed interface Decision {
        data object Fire : Decision
        data class Hold(val reason: ShutterHoldReason) : Decision
    }

    enum class ShutterHoldReason(val code: String) {
        NOT_ARMED("not_armed"),
        REQUEST_NOT_HOLDING("request_not_holding"),
        LATE_MARKED("late_marked"),
        NOT_ACQUIRED("not_acquired"),
        STALE_OR_MISSING_POSE("stale_or_missing_pose"),
        DWELL_NOT_READY("dwell_not_ready"),
        ORIENTATION("orientation"),
        DISTANCE("distance"),
        QUALITY("quality"),
        BOARD_MISMATCH("board_mismatch"),
        PROFILE_MISMATCH("profile_mismatch"),
        GRANT_NOT_LIVE("grant_not_live"),
        NOT_FOREGROUND("not_foreground"),
        ALREADY_FIRED("already_fired"),
    }

    fun decide(input: Input): Decision {
        if (input.session != SessionState.ACTIVE) return hold(ShutterHoldReason.NOT_ARMED)
        if (input.request != RequestState.HOLD_STILL) return hold(ShutterHoldReason.REQUEST_NOT_HOLDING)
        if (input.lateMark != null) return hold(ShutterHoldReason.LATE_MARKED)
        if (input.registration != RegistrationState.ACQUIRED) return hold(ShutterHoldReason.NOT_ACQUIRED)
        val age = input.poseAgeMs
        if (age == null || age < 0 || age > GuidanceHypothesis1.POSE_AGE_MS) {
            return hold(ShutterHoldReason.STALE_OR_MISSING_POSE)
        }
        if (input.dwell != DwellState.Ready) return hold(ShutterHoldReason.DWELL_NOT_READY)
        if (!input.orientationOk) return hold(ShutterHoldReason.ORIENTATION)
        if (!input.distanceOk) return hold(ShutterHoldReason.DISTANCE)
        if (!input.qualityOk) return hold(ShutterHoldReason.QUALITY)
        if (input.boardId == null || input.boardId != input.targetBoardId) {
            return hold(ShutterHoldReason.BOARD_MISMATCH)
        }
        if (input.requiredProfileId != null && input.profileId != input.requiredProfileId) {
            return hold(ShutterHoldReason.PROFILE_MISMATCH)
        }
        if (!input.grantLive) return hold(ShutterHoldReason.GRANT_NOT_LIVE)
        if (!input.foreground) return hold(ShutterHoldReason.NOT_FOREGROUND)
        if (input.alreadyFiredForArming) return hold(ShutterHoldReason.ALREADY_FIRED)
        return Decision.Fire
    }

    private fun hold(reason: ShutterHoldReason) = Decision.Hold(reason)
}
