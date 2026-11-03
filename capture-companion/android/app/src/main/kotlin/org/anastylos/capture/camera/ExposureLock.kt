package org.anastylos.capture.camera

/**
 * Outcome of locking focus, exposure and white balance before a recording. A partial or failed
 * lock never blocks recording: [note] says what was not honoured and is shown to the operator.
 */
data class ExposureLockResult(
    val aeLocked: Boolean,
    val awbLocked: Boolean,
    val afLocked: Boolean,
    /** Why something is not locked (null when everything asked for is locked). */
    val note: String? = null,
) {
    val fullyLocked: Boolean get() = aeLocked && awbLocked && afLocked

    companion object {
        fun failed(reason: String) = ExposureLockResult(aeLocked = false, awbLocked = false, afLocked = false, note = reason)
    }
}

/** What is asked of the camera to hold the lock; kept as plain values and turned into request options on use. */
data class LockConfig(val aeLock: Boolean, val awbLock: Boolean, val afMode: Int?, val focusDiopters: Float?)

/** What the camera reports about its 3A state, read from capture results (Camera2 values). */
data class LockObservation(
    val aeLock: Boolean?,
    val awbLock: Boolean?,
    val afState: Int?,
    val aeState: Int?,
    val awbState: Int?,
    val afMode: Int? = null,
)

// CaptureResult.CONTROL_AF_STATE_FOCUSED_LOCKED / NOT_FOCUSED_LOCKED and the AF modes that keep the lens still.
private const val AF_FOCUSED_LOCKED = 4
private const val AF_NOT_FOCUSED_LOCKED = 5
private const val AF_MODE_OFF = 0
private const val AF_MODE_AUTO = 1
private const val AE_STATE_LOCKED = 3
private const val AWB_STATE_LOCKED = 3

/**
 * Judges what actually holds: a lock counts only if the camera reports it back, not merely because it was
 * requested. [wantAf]/[wantAe]/[wantAwb] are false where the lock does not apply (manual focus or exposure,
 * or the sensor offers no such lock); those count as satisfied.
 */
fun judgeLock(observed: LockObservation?, wantAe: Boolean, wantAwb: Boolean, wantAf: Boolean, notes: List<String>): ExposureLockResult {
    if (observed == null) return ExposureLockResult.failed("the camera reported no state")
    val ae = !wantAe || observed.aeLock == true || observed.aeState == AE_STATE_LOCKED
    val awb = !wantAwb || observed.awbLock == true || observed.awbState == AWB_STATE_LOCKED
    // Focus is held when the AF state machine says locked, or when the lens is parked: AF off at the measured
    // distance, or AUTO mode, which never moves the lens without a trigger.
    val af = !wantAf || observed.afState == AF_FOCUSED_LOCKED || observed.afState == AF_NOT_FOCUSED_LOCKED ||
        observed.afMode == AF_MODE_OFF || observed.afMode == AF_MODE_AUTO
    val problems = buildList {
        addAll(notes)
        if (!ae) add("exposure lock was not reported by the camera")
        if (!awb) add("white-balance lock was not reported by the camera")
        if (!af) add("focus is not held")
    }
    return ExposureLockResult(ae, awb, af, problems.joinToString("; ").ifEmpty { null })
}
