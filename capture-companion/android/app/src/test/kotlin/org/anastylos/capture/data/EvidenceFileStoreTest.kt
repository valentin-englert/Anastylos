package org.anastylos.capture.data

import com.google.common.truth.Truth.assertThat
import java.io.File
import kotlinx.coroutines.test.runTest
import org.anastylos.capture.data.files.EvidenceFileStore
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class EvidenceFileStoreTest {
    @get:Rule
    val folder = TemporaryFolder()

    private fun store() = EvidenceFileStore(folder.root)

    @Test
    fun `stored originals land atomically with exact bytes`() = runTest {
        val bytes = ByteArray(10_000) { (it % 251).toByte() }
        val file = store().storeOriginal(bytes, "obs", "img", "jpg")
        assertThat(file.readBytes()).isEqualTo(bytes)
        assertThat(file.parentFile.list()).asList().containsExactly("img.jpg")
    }

    @Test
    fun `no part files survive a store`() = runTest {
        store().storeOriginal(byteArrayOf(1, 2, 3), "obs", "img", "jpg")
        val leftovers = folder.root.walkTopDown().filter { it.extension == "part" }.toList()
        assertThat(leftovers).isEmpty()
    }

    @Test
    fun `layout separates originals and previews`() {
        val store = store()
        assertThat(store.originalFile("o", "i", "jpg").path)
            .endsWith("originals${File.separator}o${File.separator}i.jpg")
        assertThat(store.previewFile("o", "i").path)
            .endsWith("previews${File.separator}o${File.separator}i.jpg")
    }

    @Test
    fun `sample size halves powers of two`() {
        assertThat(EvidenceFileStore.sampleSize(800, 600, 1024)).isEqualTo(1)
        assertThat(EvidenceFileStore.sampleSize(4000, 3000, 1024)).isEqualTo(2)
        assertThat(EvidenceFileStore.sampleSize(5000, 100, 1000)).isEqualTo(4)
    }

    @Test
    fun `mime maps to evidence extensions`() {
        assertThat(EvidenceFileStore.extensionForMime("image/jpeg")).isEqualTo("jpg")
        assertThat(EvidenceFileStore.extensionForMime("image/png")).isEqualTo("png")
        assertThat(EvidenceFileStore.extensionForMime("image/heic")).isEqualTo("heic")
        try {
            EvidenceFileStore.extensionForMime("image/gif")
            throw AssertionError("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
            // Expected: the validator accepts no GIF evidence.
        }
    }

    @Test
    fun `deleting one image keeps its siblings`() = runTest {
        val store = store()
        store.storeOriginal(byteArrayOf(1), "obs", "keep", "jpg")
        store.storeOriginal(byteArrayOf(2), "obs", "drop", "jpg")
        store.previewFile("obs", "drop").apply { parentFile?.mkdirs(); writeBytes(byteArrayOf(9)) }
        store.deleteImageFiles("obs", "drop")
        assertThat(store.originalFile("obs", "keep", "jpg").exists()).isTrue()
        assertThat(store.originalFile("obs", "drop", "jpg").exists()).isFalse()
        assertThat(store.previewFile("obs", "drop").exists()).isFalse()
    }
}
