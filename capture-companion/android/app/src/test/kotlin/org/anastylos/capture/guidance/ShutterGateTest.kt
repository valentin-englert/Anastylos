package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * §7 shutter-safety scenarios. Every precondition has a dedicated
 * hold case; only the fully-qualified input fires.
 */
class ShutterGateTest {
    private fun armed(): ShutterGate.Input = ShutterGate.Input(
        session = SessionState.ACTIVE,
        request = RequestState.HOLD_STILL,
        lateMark = null,
        registration = RegistrationState.ACQUIRED,
        poseAgeMs = 40L,
        dwell = DwellState.Ready,
        orientationOk = true,
        distanceOk = true,
        qualityOk = true,
        boardId = "surface_a3",
        targetBoardId = "surface_a3",
        profileId = "factory-0",
        requiredProfileId = null,
        grantLive = true,
        foreground = true,
        alreadyFiredForArming = false,
    )

    @Test
    fun fullyQualifiedFires() {
        assertThat(ShutterGate.decide(armed())).isEqualTo(ShutterGate.Decision.Fire)
    }

    @Test
    fun holdReasons() {
        val base = armed()
        val cases = listOf(
            (base.copy(session = SessionState.DISARMED) to ShutterGate.ShutterHoldReason.NOT_ARMED),
            (base.copy(session = SessionState.PAUSED) to ShutterGate.ShutterHoldReason.NOT_ARMED),
            (base.copy(request = RequestState.ALIGNING) to ShutterGate.ShutterHoldReason.REQUEST_NOT_HOLDING),
            (base.copy(request = RequestState.NAVIGATING) to ShutterGate.ShutterHoldReason.REQUEST_NOT_HOLDING),
            (base.copy(lateMark = LateMark.AFTER_CANCEL) to ShutterGate.ShutterHoldReason.LATE_MARKED),
            (base.copy(lateMark = LateMark.AFTER_EXPIRE) to ShutterGate.ShutterHoldReason.LATE_MARKED),
            (base.copy(lateMark = LateMark.AFTER_REVOKE) to ShutterGate.ShutterHoldReason.LATE_MARKED),
            (base.copy(registration = RegistrationState.LOST) to ShutterGate.ShutterHoldReason.NOT_ACQUIRED),
            (base.copy(registration = RegistrationState.DEGRADED) to ShutterGate.ShutterHoldReason.NOT_ACQUIRED),
            (base.copy(registration = RegistrationState.UNREGISTERED) to ShutterGate.ShutterHoldReason.NOT_ACQUIRED),
            (base.copy(poseAgeMs = null) to ShutterGate.ShutterHoldReason.STALE_OR_MISSING_POSE),
            (base.copy(poseAgeMs = 151L) to ShutterGate.ShutterHoldReason.STALE_OR_MISSING_POSE),
            (base.copy(poseAgeMs = -1L) to ShutterGate.ShutterHoldReason.STALE_OR_MISSING_POSE),
            (base.copy(dwell = DwellState.Idle) to ShutterGate.ShutterHoldReason.DWELL_NOT_READY),
            (base.copy(dwell = DwellState.Accumulating(3, 400L)) to ShutterGate.ShutterHoldReason.DWELL_NOT_READY),
            (base.copy(orientationOk = false) to ShutterGate.ShutterHoldReason.ORIENTATION),
            (base.copy(distanceOk = false) to ShutterGate.ShutterHoldReason.DISTANCE),
            (base.copy(qualityOk = false) to ShutterGate.ShutterHoldReason.QUALITY),
            (base.copy(boardId = "surface_a4") to ShutterGate.ShutterHoldReason.BOARD_MISMATCH),
            (base.copy(boardId = null) to ShutterGate.ShutterHoldReason.BOARD_MISMATCH),
            (
                base.copy(requiredProfileId = "cal-9", profileId = "factory-0") to
                    ShutterGate.ShutterHoldReason.PROFILE_MISMATCH
                ),
            (base.copy(grantLive = false) to ShutterGate.ShutterHoldReason.GRANT_NOT_LIVE),
            (base.copy(foreground = false) to ShutterGate.ShutterHoldReason.NOT_FOREGROUND),
            (base.copy(alreadyFiredForArming = true) to ShutterGate.ShutterHoldReason.ALREADY_FIRED),
        )
        for ((input, reason) in cases) {
            assertThat(ShutterGate.decide(input)).isEqualTo(ShutterGate.Decision.Hold(reason))
        }
    }

    @Test
    fun poseAgeBoundary() {
        assertThat(ShutterGate.decide(armed().copy(poseAgeMs = 150L)))
            .isEqualTo(ShutterGate.Decision.Fire)
    }

    @Test
    fun firstViolationWins() {
        // Session is checked before the request: a disarmed session
        // with a wandering request still reports NOT_ARMED.
        val decision = ShutterGate.decide(
            armed().copy(session = SessionState.DISARMED, request = RequestState.NAVIGATING),
        )
        assertThat(decision).isEqualTo(ShutterGate.Decision.Hold(ShutterGate.ShutterHoldReason.NOT_ARMED))
    }

    @Test
    fun requiredProfileMatchFires() {
        val decision = ShutterGate.decide(
            armed().copy(requiredProfileId = "cal-9", profileId = "cal-9"),
        )
        assertThat(decision).isEqualTo(ShutterGate.Decision.Fire)
    }
}
