package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class YuvFrameTest {
    @Test
    fun tightCopyWhenStrideEqualsWidth() {
        val src = ByteArray(12) { it.toByte() }
        assertThat(YuvFrame.copyYPlane(src, 4, 3, 4)).isEqualTo(src)
    }

    @Test
    fun stridedCopySkipsPadding() {
        // 3x2 frame, row stride 4 (one padding byte per row).
        val src = byteArrayOf(1, 2, 3, 99, 4, 5, 6, 99)
        assertThat(YuvFrame.copyYPlane(src, 3, 2, 4))
            .isEqualTo(byteArrayOf(1, 2, 3, 4, 5, 6))
    }

    @Test
    fun shortBufferRejected() {
        try {
            YuvFrame.copyYPlane(ByteArray(3), 2, 2, 2)
            throw AssertionError("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
        }
    }

    @Test
    fun narrowStrideRejected() {
        try {
            YuvFrame.copyYPlane(ByteArray(8), 4, 2, 3)
            throw AssertionError("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
        }
    }
}
