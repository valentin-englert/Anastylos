package org.anastylos.capture.camera

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class ExposureLockTest {
    private fun observed(aeLock: Boolean? = true, awbLock: Boolean? = true, af: Int? = 4, ae: Int? = 3, awb: Int? = 3, afMode: Int? = 4) =
        LockObservation(aeLock, awbLock, af, ae, awb, afMode)

    @Test
    fun aLockCountsOnlyWhenTheCameraReportsItBack() {
        val ok = judgeLock(observed(), wantAe = true, wantAwb = true, wantAf = true, notes = emptyList())
        assertThat(ok.fullyLocked).isTrue()
        assertThat(ok.note).isNull()
        val unreported = judgeLock(observed(aeLock = false, ae = 2), wantAe = true, wantAwb = true, wantAf = true, notes = emptyList())
        assertThat(unreported.aeLocked).isFalse()
        assertThat(unreported.note).contains("exposure lock was not reported")
    }

    @Test
    fun theLockedStateOfTheStateMachineCountsEvenWithoutTheRequestEcho() {
        val result = judgeLock(observed(aeLock = null, awbLock = null), wantAe = true, wantAwb = true, wantAf = true, notes = emptyList())
        assertThat(result.fullyLocked).isTrue()
    }

    @Test
    fun focusMustBeHeldNotHunting() {
        val hunting = judgeLock(observed(af = 1, afMode = 4), wantAe = true, wantAwb = true, wantAf = true, notes = emptyList())
        assertThat(hunting.afLocked).isFalse()
        assertThat(hunting.note).contains("focus is not held")
        val notFocusedButLocked = judgeLock(observed(af = 5), wantAe = true, wantAwb = true, wantAf = true, notes = emptyList())
        assertThat(notFocusedButLocked.afLocked).isTrue()
        // A parked lens (AF off, or AUTO without a trigger) holds focus although the state machine is inactive.
        assertThat(judgeLock(observed(af = 0, afMode = 0), true, true, true, emptyList()).afLocked).isTrue()
        assertThat(judgeLock(observed(af = 0, afMode = 1), true, true, true, emptyList()).afLocked).isTrue()
    }

    @Test
    fun locksThatDoNotApplyAreSatisfiedAndNotesAreKept() {
        val manual = judgeLock(observed(aeLock = false, ae = 1, af = 0), wantAe = false, wantAwb = true, wantAf = false, notes = listOf("note"))
        assertThat(manual.fullyLocked).isTrue()
        assertThat(manual.note).isEqualTo("note")
        assertThat(judgeLock(null, true, true, true, emptyList()).fullyLocked).isFalse()
    }
}
