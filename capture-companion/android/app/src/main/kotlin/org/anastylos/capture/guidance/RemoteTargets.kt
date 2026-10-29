package org.anastylos.capture.guidance

/**
 * Converts a Core request target into a local guidance viewpoint.
 * Returns null (fail closed, no adoption) when the wire target is
 * malformed: wrong units, non-finite matrix, invalid rotation, or
 * the camera behind the board plane. Tolerances fall back to the
 * frozen profile when the wire values are absent or malformed —
 * narrowing an unknown tolerance would invent precision.
 */
fun cameraPoseFromTargetMatrix(tBC: List<Double>): CameraPoseB? {
    if (tBC.size != 16 || tBC.any { !it.isFinite() }) return null
    if (tBC[12] != 0.0 || tBC[13] != 0.0 || tBC[14] != 0.0 || tBC[15] != 1.0) return null
    val rotation = rotationOfRowMajor(tBC)
    if (!PoseMath.isValidRotation(rotation)) return null
    val center = translationOfRowMajor(tBC)
    if (center.z >= 0) return null
    val (yaw, pitch, roll) = eulerYXZDeg(rotation)
    if (!yaw.isFinite() || !pitch.isFinite() || !roll.isFinite()) return null
    return CameraPoseB(center, yaw, pitch, roll)
}

data class RemoteTargetView(
    val id: String,
    val label: String,
    val boardFrameId: String,
    val pose: CameraPoseB,
    val posTolMm: Vec3,
    val angTolDeg: Double,
)

fun remoteTargetView(
    requestId: String,
    targetLabel: String,
    boardFrameId: String,
    tbcUnits: String,
    tbcMatrix: List<Double>,
    translationToleranceMm: List<Double>,
    rotationToleranceDeg: Double,
): RemoteTargetView? {
    if (tbcUnits != "mm") return null
    val pose = cameraPoseFromTargetMatrix(tbcMatrix) ?: return null
    val tol = if (translationToleranceMm.size == 3 &&
        translationToleranceMm.all { it.isFinite() && it > 0 }
    ) {
        Vec3(translationToleranceMm[0], translationToleranceMm[1], translationToleranceMm[2])
    } else {
        Vec3(
            GuidanceHypothesis1.POS_TOL_X_MM,
            GuidanceHypothesis1.POS_TOL_Y_MM,
            GuidanceHypothesis1.POS_TOL_Z_MM,
        )
    }
    val ang = if (rotationToleranceDeg.isFinite() && rotationToleranceDeg > 0) {
        rotationToleranceDeg
    } else {
        GuidanceHypothesis1.ANG_TOL_DEG
    }
    return RemoteTargetView(
        id = "remote-$requestId",
        label = targetLabel.ifBlank { "remote request" },
        boardFrameId = boardFrameId,
        pose = pose,
        posTolMm = tol,
        angTolDeg = ang,
    )
}
