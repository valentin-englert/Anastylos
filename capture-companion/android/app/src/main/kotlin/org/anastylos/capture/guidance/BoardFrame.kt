package org.anastylos.capture.guidance

import kotlin.math.acos
import kotlin.math.asin
import kotlin.math.atan2

/**
 * Board measurement frame, preserved end-to-end on the phone:
 * x right, y down, z into the board plane (away from the viewer).
 * Right-handed, millimetres. The phone never converts this to a
 * z-up CAD frame; that rotation belongs to the CAD lane.
 */
data class Vec3(val x: Double, val y: Double, val z: Double) {
    operator fun minus(other: Vec3) = Vec3(x - other.x, y - other.y, z - other.z)
}

/**
 * Camera pose in the board frame: optical-center position plus
 * orientation as yaw/pitch/roll in degrees.
 *
 * - yaw: right-hand rotation about board +y (down axis). Positive
 *   yaw tips the view toward +x (board-right).
 * - pitch: right-hand rotation about board +x. Positive pitch tips
 *   the view toward -y (board-up).
 * - roll: right-hand rotation about board +z (view axis). Positive
 *   roll is clockwise as seen in the live preview.
 *
 * Matrix convention: R = Ry(yaw) * Rx(pitch) * Rz(roll).
 */
data class CameraPoseB(
    val centerMm: Vec3,
    val yawDeg: Double = 0.0,
    val pitchDeg: Double = 0.0,
    val rollDeg: Double = 0.0,
)

/** Translation (camera center) of a row-major 4x4 T_B_C matrix. */
fun translationOfRowMajor(tBC: List<Double>): Vec3 {
    require(tBC.size == 16) { "T_B_C must hold 16 row-major doubles" }
    return Vec3(tBC[3], tBC[7], tBC[11])
}

/** Upper-left 3x3 rotation of a row-major 4x4 T_B_C matrix. */
fun rotationOfRowMajor(tBC: List<Double>): List<Double> {
    require(tBC.size == 16) { "T_B_C must hold 16 row-major doubles" }
    return listOf(tBC[0], tBC[1], tBC[2], tBC[4], tBC[5], tBC[6], tBC[8], tBC[9], tBC[10])
}

/**
 * Yaw/pitch/roll in degrees from a row-major 3x3 rotation, using the
 * R = Ry * Rx * Rz convention above. At the pitch = ±90° gimbal
 * lock the yaw is reported as 0 and the combined twist as roll;
 * deterministic, documented, and covered by tests.
 */
fun eulerYXZDeg(r: List<Double>): Triple<Double, Double, Double> {
    require(r.size == 9) { "rotation must hold 9 row-major doubles" }
    val radToDeg = 180.0 / Math.PI
    val sp = (-r[5]).coerceIn(-1.0, 1.0)
    val pitch = asin(sp) * radToDeg
    if (kotlin.math.abs(r[5]) >= 1.0 - 1e-9) {
        return Triple(0.0, pitch, atan2(-r[1], r[0]) * radToDeg)
    }
    val yaw = atan2(r[2], r[8]) * radToDeg
    val roll = atan2(r[3], r[4]) * radToDeg
    return Triple(yaw, pitch, roll)
}

/** Smallest angle in degrees between two row-major 3x3 rotations. */
fun rotationAngleBetweenDeg(a: List<Double>, b: List<Double>): Double {
    require(a.size == 9 && b.size == 9) { "rotations must hold 9 row-major doubles" }
    // Trace of A^T * B = sum over columns of dot products.
    var trace = 0.0
    for (col in 0..2) {
        for (row in 0..2) {
            trace += a[row * 3 + col] * b[row * 3 + col]
        }
    }
    return acos(((trace - 1.0) / 2.0).coerceIn(-1.0, 1.0)) * 180.0 / Math.PI
}

fun wrap180Deg(degrees: Double): Double {
    val wrapped = (degrees + 180.0) % 360.0
    return (if (wrapped < 0) wrapped + 360.0 else wrapped) - 180.0
}

/**
 * Clockwise degrees the phone is turned from the reference attitude
 * where board-up aligns with screen-up. The cue layer rotates
 * board-frame deltas into screen-frame words through this; the
 * solver itself always works in unrotated analysis coordinates.
 */
enum class PhoneRotation(val clockwiseDeg: Int) {
    R0(0),
    R90(90),
    R180(180),
    R270(270),
}

/**
 * Board-frame delta to screen-frame (x right, y up) cue vector. A
 * phone turned clockwise by θ shows a fixed world vector rotated
 * counter-clockwise by θ, hence the CCW maps below.
 */
fun toScreenFrame(xMm: Double, yUpMm: Double, rotation: PhoneRotation): Pair<Double, Double> =
    when (rotation) {
        PhoneRotation.R0 -> xMm to yUpMm
        PhoneRotation.R90 -> (-yUpMm) to xMm
        PhoneRotation.R180 -> (-xMm) to (-yUpMm)
        PhoneRotation.R270 -> yUpMm to (-xMm)
    }

enum class CueAxis { LATERAL, VERTICAL, DEPTH, YAW, PITCH, ROLL }

/**
 * One operator movement cue: which axis, the screen-relative word,
 * and the remaining magnitude (mm for translation, degrees for
 * rotation). Translation words match the frozen guidance-signs
 * vectors exactly (`right/left/up/down/closer/back`).
 */
data class MovementCue(val axis: CueAxis, val word: String, val magnitude: Double) {
    fun render(): String = "$word ${kotlin.math.round(magnitude).toInt()}"
}

/**
 * Screen-relative movement cues that reduce the pose error. Axes
 * already within tolerance produce no cue; an empty list means
 * aligned. Rotation words name the rigid phone motion the operator
 * must apply (the camera view follows the phone 1:1): positive yaw
 * error pans the view right so the operator turns right, positive
 * pitch error tips the view up so the operator tilts up, positive
 * roll error is clockwise as seen in the preview.
 */
fun computeCues(
    current: CameraPoseB,
    desired: CameraPoseB,
    rotation: PhoneRotation,
    posTolMm: Vec3,
    angTolDeg: Double,
): List<MovementCue> {
    val cues = mutableListOf<MovementCue>()
    val delta = desired.centerMm - current.centerMm
    val (sx, syUp) = toScreenFrame(delta.x, -delta.y, rotation)
    if (kotlin.math.abs(sx) > posTolMm.x) {
        cues += MovementCue(CueAxis.LATERAL, if (sx > 0) "right" else "left", kotlin.math.abs(sx))
    }
    if (kotlin.math.abs(syUp) > posTolMm.y) {
        cues += MovementCue(CueAxis.VERTICAL, if (syUp > 0) "up" else "down", kotlin.math.abs(syUp))
    }
    if (kotlin.math.abs(delta.z) > posTolMm.z) {
        cues += MovementCue(CueAxis.DEPTH, if (delta.z > 0) "closer" else "back", kotlin.math.abs(delta.z))
    }
    val yawErr = wrap180Deg(desired.yawDeg - current.yawDeg)
    if (kotlin.math.abs(yawErr) > angTolDeg) {
        cues += MovementCue(CueAxis.YAW, if (yawErr > 0) "turn right" else "turn left", kotlin.math.abs(yawErr))
    }
    val pitchErr = wrap180Deg(desired.pitchDeg - current.pitchDeg)
    if (kotlin.math.abs(pitchErr) > angTolDeg) {
        cues += MovementCue(CueAxis.PITCH, if (pitchErr > 0) "tilt up" else "tilt down", kotlin.math.abs(pitchErr))
    }
    val rollErr = wrap180Deg(desired.rollDeg - current.rollDeg)
    if (kotlin.math.abs(rollErr) > angTolDeg) {
        cues += MovementCue(
            CueAxis.ROLL,
            if (rollErr > 0) "roll clockwise" else "roll counter-clockwise",
            kotlin.math.abs(rollErr),
        )
    }
    return cues
}
