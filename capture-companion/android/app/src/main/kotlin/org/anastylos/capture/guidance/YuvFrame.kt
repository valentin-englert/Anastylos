package org.anastylos.capture.guidance

/**
 * Pure-JVM Y-plane handling for analysis buffers. Copies the luma
 * plane out of a strided row-major source into a tight buffer for
 * the solver. No rotation: the solver is rotation-invariant (see
 * PoseMath), so native buffer coordinates are used at every device
 * orientation. The device adapter only supplies raw plane bytes
 * plus geometry.
 */
object YuvFrame {
    /**
     * Tight `width x height` luma copy from a row-major source with
     * `rowStride >= width`. Throws on short buffers.
     */
    fun copyYPlane(src: ByteArray, width: Int, height: Int, rowStride: Int): ByteArray {
        require(width > 0 && height > 0) { "need positive dimensions" }
        require(rowStride >= width) { "rowStride must cover the row width" }
        require(src.size >= rowStride * (height - 1) + width) { "Y plane buffer too small" }
        if (rowStride == width) return src.copyOf(width * height)
        val out = ByteArray(width * height)
        for (row in 0 until height) {
            src.copyInto(out, row * width, row * rowStride, row * rowStride + width)
        }
        return out
    }

}
