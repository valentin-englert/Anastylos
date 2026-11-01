package org.anastylos.capture.guidance

import kotlin.math.sqrt

/**
 * First-order (1 sigma) uncertainty of a PnP solution, reported in the frame
 * the sidecar claims: the camera centre in board millimetres and the rotation
 * angle in degrees.
 *
 * Gauss-Newton at the solution: `cov(rvec, tvec) = s² (JᵀJ)⁻¹`, with `J` the
 * reprojection Jacobian (central differences through [PoseMath.projectPoints])
 * and `s²` the residual variance over `2N − 6` degrees of freedom. The camera
 * centre `C = −Rᵀ t` is propagated with its own numeric Jacobian. Valid only
 * near a well-conditioned minimum; null when the normal matrix is singular or
 * a point projects behind the camera.
 */
object PoseUncertainty {
    const val METHOD = "gauss-newton-jtj-1sigma (residual variance, central differences)"

    data class Result(val positionMm: List<Double>, val angleDeg: Double)

    fun of(
        correspondences: List<PoseMath.Correspondence>,
        rvec: List<Double>,
        tvec: List<Double>,
        intrinsics: PoseEstimator.Intrinsics,
    ): Result? {
        val n = correspondences.size
        if (n < 4) return null
        val params = rvec + tvec
        val base = residuals(correspondences, params, intrinsics) ?: return null
        val sigma2 = base.sumOf { it * it } / (2 * n - 6)
        val jacobian = Array(2 * n) { DoubleArray(6) }
        for (p in 0 until 6) {
            val h = if (p < 3) 1e-6 else 1e-4
            val plus = residuals(correspondences, params.bump(p, h), intrinsics) ?: return null
            val minus = residuals(correspondences, params.bump(p, -h), intrinsics) ?: return null
            for (row in 0 until 2 * n) jacobian[row][p] = (plus[row] - minus[row]) / (2 * h)
        }
        val normal = Array(6) { i -> DoubleArray(6) { j -> (0 until 2 * n).sumOf { jacobian[it][i] * jacobian[it][j] } } }
        val inverse = invert(normal) ?: return null
        val cov = Array(6) { i -> DoubleArray(6) { j -> sigma2 * inverse[i][j] } }

        // Camera centre in the board frame and its Jacobian over (rvec, tvec).
        val centre = { q: List<Double> -> cameraCentre(q.subList(0, 3), q.subList(3, 6)) }
        val jc = Array(3) { DoubleArray(6) }
        for (p in 0 until 6) {
            val h = if (p < 3) 1e-6 else 1e-4
            val plus = centre(params.bump(p, h))
            val minus = centre(params.bump(p, -h))
            for (axis in 0 until 3) jc[axis][p] = (plus[axis] - minus[axis]) / (2 * h)
        }
        val position = (0 until 3).map { axis ->
            var variance = 0.0
            for (i in 0 until 6) for (j in 0 until 6) variance += jc[axis][i] * cov[i][j] * jc[axis][j]
            sqrt(variance.coerceAtLeast(0.0))
        }
        val angle = Math.toDegrees(sqrt((cov[0][0] + cov[1][1] + cov[2][2]).coerceAtLeast(0.0)))
        if (position.any { !it.isFinite() } || !angle.isFinite()) return null
        return Result(position, angle)
    }

    private fun residuals(
        correspondences: List<PoseMath.Correspondence>,
        params: List<Double>,
        intrinsics: PoseEstimator.Intrinsics,
    ): DoubleArray? {
        val projected = PoseMath.projectPoints(
            correspondences.map { it.objectMm }, params.subList(0, 3), params.subList(3, 6),
            intrinsics.fx, intrinsics.fy, intrinsics.cx, intrinsics.cy, intrinsics.distortion,
        )
        val out = DoubleArray(2 * correspondences.size)
        for (index in correspondences.indices) {
            val prediction = projected[index] ?: return null
            out[2 * index] = prediction[0] - correspondences[index].imagePx[0]
            out[2 * index + 1] = prediction[1] - correspondences[index].imagePx[1]
        }
        return out
    }

    private fun cameraCentre(rvec: List<Double>, tvec: List<Double>): List<Double> {
        val r = PoseMath.rodriguesToMatrix(rvec)
        return (0 until 3).map { col -> -(r[col] * tvec[0] + r[3 + col] * tvec[1] + r[6 + col] * tvec[2]) }
    }

    private fun List<Double>.bump(index: Int, delta: Double): List<Double> =
        mapIndexed { i, v -> if (i == index) v + delta else v }

    /** Gauss-Jordan with partial pivoting; null when singular. */
    private fun invert(matrix: Array<DoubleArray>): Array<DoubleArray>? {
        val size = matrix.size
        val a = Array(size) { i -> DoubleArray(2 * size) { j -> if (j < size) matrix[i][j] else if (j - size == i) 1.0 else 0.0 } }
        for (col in 0 until size) {
            val pivot = (col until size).maxByOrNull { kotlin.math.abs(a[it][col]) } ?: return null
            if (kotlin.math.abs(a[pivot][col]) < 1e-12) return null
            val tmp = a[col]; a[col] = a[pivot]; a[pivot] = tmp
            val div = a[col][col]
            for (j in 0 until 2 * size) a[col][j] /= div
            for (row in 0 until size) {
                if (row == col) continue
                val factor = a[row][col]
                if (factor != 0.0) for (j in 0 until 2 * size) a[row][j] -= factor * a[col][j]
            }
        }
        return Array(size) { i -> DoubleArray(size) { j -> a[i][size + j] } }
    }
}
