package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Guidance cue math. The frozen-vector cases below are literals of
 * `capture-companion/control/contracts/vectors/guidance-signs.json`
 * (contract ec313f7f); a drift check in the Wave-1 evidence report
 * verifies them byte-equal against the frozen file. Vectors pin cue
 * math with a tight explicit tolerance, not the hypothesis
 * tolerances.
 */
class CuesTest {
    private val vectorPose = listOf(
        1.0, 0.0, 0.0, 100.0,
        0.0, 1.0, 0.0, 80.0,
        0.0, 0.0, 1.0, -400.0,
        0.0, 0.0, 0.0, 1.0,
    )
    private val tightTol = Vec3(0.5, 0.5, 0.5)
    private val current = CameraPoseB(centerMm = Vec3(100.0, 80.0, -400.0))

    private fun cuesFor(desired: Vec3, rotation: PhoneRotation = PhoneRotation.R0): List<String> =
        computeCues(current, CameraPoseB(centerMm = desired), rotation, tightTol, 0.5)
            .map { it.render() }

    @Test
    fun frozenVectorCase1() {
        assertThat(cuesFor(Vec3(120.0, 70.0, -450.0)))
            .containsExactly("right 20", "up 10", "back 50").inOrder()
    }

    @Test
    fun frozenVectorCase2() {
        assertThat(cuesFor(Vec3(100.0, 80.0, -350.0))).containsExactly("closer 50")
    }

    @Test
    fun vectorTranslationParsesFromRowMajor() {
        assertThat(translationOfRowMajor(vectorPose)).isEqualTo(Vec3(100.0, 80.0, -400.0))
    }

    @Test
    fun vectorRotationIsIdentity() {
        val (yaw, pitch, roll) = eulerYXZDeg(rotationOfRowMajor(vectorPose))
        assertThat(yaw).isWithin(1e-9).of(0.0)
        assertThat(pitch).isWithin(1e-9).of(0.0)
        assertThat(roll).isWithin(1e-9).of(0.0)
    }

    @Test
    fun samePoseIsAligned() {
        assertThat(cuesFor(Vec3(100.0, 80.0, -400.0))).isEmpty()
    }

    @Test
    fun leftDownBackSigns() {
        assertThat(cuesFor(Vec3(80.0, 95.0, -430.0)))
            .containsExactly("left 20", "down 15", "back 30").inOrder()
    }

    @Test
    fun inToleranceAxesProduceNoCue() {
        val cues = computeCues(
            current,
            CameraPoseB(centerMm = Vec3(100.4, 80.4, -400.4)),
            PhoneRotation.R0,
            tightTol,
            0.5,
        )
        assertThat(cues).isEmpty()
    }

    @Test
    fun rotation90TurnsRightIntoUp() {
        assertThat(cuesFor(Vec3(110.0, 80.0, -400.0), PhoneRotation.R90))
            .containsExactly("up 10")
    }

    @Test
    fun rotation180TurnsRightIntoLeft() {
        assertThat(cuesFor(Vec3(110.0, 80.0, -400.0), PhoneRotation.R180))
            .containsExactly("left 10")
    }

    @Test
    fun rotation270TurnsRightIntoDown() {
        assertThat(cuesFor(Vec3(110.0, 80.0, -400.0), PhoneRotation.R270))
            .containsExactly("down 10")
    }

    @Test
    fun boardUpRotatesThroughAllQuadrants() {
        // Desired 10 mm board-up (-y) from the vector camera center.
        val desired = Vec3(100.0, 70.0, -400.0)
        assertThat(cuesFor(desired, PhoneRotation.R0)).containsExactly("up 10")
        assertThat(cuesFor(desired, PhoneRotation.R90)).containsExactly("left 10")
        assertThat(cuesFor(desired, PhoneRotation.R180)).containsExactly("down 10")
        assertThat(cuesFor(desired, PhoneRotation.R270)).containsExactly("right 10")
    }

    @Test
    fun depthUnaffectedByRotation() {
        val desired = Vec3(100.0, 80.0, -380.0)
        PhoneRotation.entries.forEach { rotation ->
            assertThat(cuesFor(desired, rotation)).containsExactly("closer 20")
        }
    }

    @Test
    fun yawCuesTurnIntoTheError() {
        val tol = Vec3(1000.0, 1000.0, 1000.0)
        val right = computeCues(current, current.copy(yawDeg = 10.0), PhoneRotation.R0, tol, 5.0)
        assertThat(right.map { it.render() }).containsExactly("turn right 10")
        val left = computeCues(current, current.copy(yawDeg = -10.0), PhoneRotation.R0, tol, 5.0)
        assertThat(left.map { it.render() }).containsExactly("turn left 10")
    }

    @Test
    fun pitchCuesTiltIntoTheError() {
        val tol = Vec3(1000.0, 1000.0, 1000.0)
        val up = computeCues(current, current.copy(pitchDeg = 8.0), PhoneRotation.R0, tol, 5.0)
        assertThat(up.map { it.render() }).containsExactly("tilt up 8")
        val down = computeCues(current, current.copy(pitchDeg = -8.0), PhoneRotation.R0, tol, 5.0)
        assertThat(down.map { it.render() }).containsExactly("tilt down 8")
    }

    @Test
    fun rollCuesRotateIntoTheError() {
        val tol = Vec3(1000.0, 1000.0, 1000.0)
        val cw = computeCues(current, current.copy(rollDeg = 10.0), PhoneRotation.R0, tol, 5.0)
        assertThat(cw.map { it.render() }).containsExactly("roll clockwise 10")
        val ccw = computeCues(current, current.copy(rollDeg = -10.0), PhoneRotation.R0, tol, 5.0)
        assertThat(ccw.map { it.render() }).containsExactly("roll counter-clockwise 10")
    }

    @Test
    fun yawWrapsInsteadOfSpinning() {
        val tol = Vec3(1000.0, 1000.0, 1000.0)
        val from = current.copy(yawDeg = 179.0)
        val to = current.copy(yawDeg = -179.0)
        // +2 degrees through the wrap, inside tolerance: no cue.
        assertThat(computeCues(from, to, PhoneRotation.R0, tol, 5.0)).isEmpty()
    }

    @Test
    fun pureAxisRotationsExtract() {
        val (yaw, _, _) = eulerYXZDeg(
            listOf(0.866025403784439, 0.0, 0.5, 0.0, 1.0, 0.0, -0.5, 0.0, 0.866025403784439),
        )
        assertThat(yaw).isWithin(1e-9).of(30.0)
        val (_, pitch, _) = eulerYXZDeg(
            listOf(1.0, 0.0, 0.0, 0.0, 0.939692620785908, -0.342020143325669, 0.0, 0.342020143325669, 0.939692620785908),
        )
        assertThat(pitch).isWithin(1e-9).of(20.0)
        val (_, _, roll) = eulerYXZDeg(
            listOf(0.965925826289068, 0.258819045102521, 0.0, -0.258819045102521, 0.965925826289068, 0.0, 0.0, 0.0, 1.0),
        )
        assertThat(roll).isWithin(1e-9).of(-15.0)
    }

    @Test
    fun composedRotationExtracts() {
        // Independently computed R = Ry(25) * Rx(-10) * Rz(5).
        val r = listOf(
            0.896462923292124, -0.152097560060841, 0.416197740726783,
            0.085831651177431, 0.981060262190407, 0.173648177666930,
            -0.434726528772625, -0.119946213672750, 0.892538935289030,
        )
        // The test matrix itself is a valid rotation.
        for (row in 0..2) {
            val norm = kotlin.math.sqrt((0..2).sumOf { col -> r[row * 3 + col] * r[row * 3 + col] })
            assertThat(norm).isWithin(1e-9).of(1.0)
        }
        val (yaw, pitch, roll) = eulerYXZDeg(r)
        assertThat(yaw).isWithin(1e-9).of(25.0)
        assertThat(pitch).isWithin(1e-9).of(-10.0)
        assertThat(roll).isWithin(1e-9).of(5.0)
    }

    @Test
    fun rotationAngleBetween() {
        val identity = listOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        val roll90 = listOf(0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        assertThat(rotationAngleBetweenDeg(identity, identity)).isWithin(1e-9).of(0.0)
        assertThat(rotationAngleBetweenDeg(identity, roll90)).isWithin(1e-9).of(90.0)
    }

    @Test
    fun cueRenderRoundsMillimetres() {
        assertThat(MovementCue(CueAxis.LATERAL, "right", 19.6).render()).isEqualTo("right 20")
    }
}
