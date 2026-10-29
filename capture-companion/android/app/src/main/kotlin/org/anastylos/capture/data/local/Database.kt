package org.anastylos.capture.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverter
import androidx.room.TypeConverters
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.time.Instant
import org.anastylos.capture.domain.parseIsoUtc
import org.anastylos.capture.domain.toIsoUtc

class InstantConverter {
    @TypeConverter
    fun fromString(value: String): Instant =
        parseIsoUtc(value) ?: throw IllegalArgumentException("not ISO 8601 UTC: $value")

    @TypeConverter
    fun toString(value: Instant): String = value.toIsoUtc()
}

/**
 * Forward-only v1 → v2: adds the `guided_associations` link table.
 * Existing tables are untouched, so pre-upgrade evidence reads back
 * byte-identical. There is no downgrade path (Room refuses to open a
 * v2 file with v1 code); rollback is reinstall, with evidence
 * preserved through the unchanged v1 tables plus file export.
 */
val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `guided_associations` (" +
                "`image_id` TEXT NOT NULL, `observation_id` TEXT NOT NULL, " +
                "`package_id` TEXT, `guided_session_id` TEXT NOT NULL, " +
                "`request_id` TEXT, `request_revision` INTEGER, " +
                "`attempt_id` TEXT, `attempt_number` INTEGER, " +
                "`sidecar_revision` INTEGER NOT NULL, " +
                "`delivery_state` TEXT NOT NULL, `receipt_status` TEXT, " +
                "`updated_at` TEXT NOT NULL, PRIMARY KEY(`image_id`), " +
                "FOREIGN KEY(`image_id`) REFERENCES `images`(`image_id`) " +
                "ON UPDATE NO ACTION ON DELETE CASCADE)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS `index_guided_associations_attempt_id` " +
                "ON `guided_associations` (`attempt_id`)",
        )
    }
}

@Database(
    entities = [SessionEntity::class, ObservationEntity::class, ImageEntity::class, GuidedAssociationEntity::class],
    version = 2,
    exportSchema = true,
)
@TypeConverters(InstantConverter::class)
abstract class CaptureDatabase : RoomDatabase() {
    abstract fun sessions(): SessionDao

    abstract fun observations(): ObservationDao

    abstract fun images(): ImageDao

    abstract fun guidedAssociations(): GuidedAssociationDao

    companion object {
        fun open(context: Context): CaptureDatabase =
            Room.databaseBuilder(context, CaptureDatabase::class.java, "capture.db")
                .addMigrations(MIGRATION_1_2)
                .build()

        /** Tests only: an empty database with no durable side effects. */
        fun openInMemory(context: Context): CaptureDatabase =
            Room.inMemoryDatabaseBuilder(context, CaptureDatabase::class.java)
                .allowMainThreadQueries()
                .build()
    }
}
