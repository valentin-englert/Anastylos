package org.anastylos.capture.voice

import com.google.common.truth.Truth.assertThat
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Test

class WavEncoderTest {
    @Test
    fun headerMatchesTranscriberContract() {
        val wav = WavEncoder.encode(ShortArray(16_000) { 7 })
        val b = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN)
        assertThat(String(wav, 0, 4)).isEqualTo("RIFF")
        assertThat(String(wav, 8, 4)).isEqualTo("WAVE")
        assertThat(b.getShort(20).toInt()).isEqualTo(1)   // PCM
        assertThat(b.getShort(22).toInt()).isEqualTo(1)   // mono
        assertThat(b.getInt(24)).isEqualTo(16_000)
        assertThat(b.getShort(34).toInt()).isEqualTo(16)
        assertThat(b.getInt(40)).isEqualTo(32_000)
        assertThat(wav.size).isEqualTo(44 + 32_000)
    }

    @Test(expected = IllegalArgumentException::class)
    fun otherRatesAreRefused() {
        WavEncoder.encode(ShortArray(10), 44_100)
    }
}
