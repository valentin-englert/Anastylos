package org.anastylos.capture.domain

import com.google.common.truth.Truth.assertThat
import java.io.File
import org.junit.Test

class HashingTest {
    @Test
    fun `sha256 of empty input matches NIST vector`() {
        assertThat(ByteArray(0).sha256Hex())
            .isEqualTo("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    }

    @Test
    fun `sha256 of abc matches NIST vector`() {
        assertThat("abc".toByteArray().sha256Hex())
            .isEqualTo("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    @Test
    fun `streaming file hash matches buffer hash`() {
        val tmp = File.createTempFile("hash", ".bin")
        try {
            tmp.writeBytes(ByteArray(200_000) { (it % 251).toByte() })
            assertThat(tmp.sha256Hex()).isEqualTo(tmp.readBytes().sha256Hex())
        } finally {
            tmp.delete()
        }
    }

    @Test
    fun `sha256 format check rejects garbage`() {
        assertThat(isSha256Hex("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")).isTrue()
        assertThat(isSha256Hex("E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855")).isFalse()
        assertThat(isSha256Hex("e3b0")).isFalse()
        assertThat(isSha256Hex("")).isFalse()
    }

    @Test
    fun `id check accepts UUIDs only`() {
        assertThat(isId("11111111-1111-4111-8111-111111111111")).isTrue()
        assertThat(isId("not-a-uuid")).isFalse()
        assertThat(isId("../config.json")).isFalse()
        assertThat(isId("")).isFalse()
    }
}
