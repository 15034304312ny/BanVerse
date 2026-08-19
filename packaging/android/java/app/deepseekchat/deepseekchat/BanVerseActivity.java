package app.deepseekchat.deepseekchat;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.util.Log;

import org.kivy.android.PythonActivity;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

public final class BanVerseActivity extends PythonActivity {
    private static final String TAG = "BanVersePicker";
    private static final String SCHEME = "banverse-picker";
    private static final int PICKER_REQUEST_CODE = 0x4B56;
    private static final long IMAGE_LIMIT = 20L * 1024L * 1024L;
    private static final long JSON_LIMIT = 2L * 1024L * 1024L;
    private static final long STALE_AGE_MS = 24L * 60L * 60L * 1000L;
    private static final String STATE_TOKEN = "banverse_picker_token";
    private static final String STATE_KIND = "banverse_picker_kind";

    private String activeToken = "";
    private String activeKind = "file";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (savedInstanceState != null) {
            activeToken = savedInstanceState.getString(STATE_TOKEN, "");
            activeKind = savedInstanceState.getString(STATE_KIND, "file");
        }
        handlePickerIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        setIntent(intent);
        if (!handlePickerIntent(intent)) {
            super.onNewIntent(intent);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        state.putString(STATE_TOKEN, activeToken);
        state.putString(STATE_KIND, activeKind);
        super.onSaveInstanceState(state);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != PICKER_REQUEST_CODE) {
            super.onActivityResult(requestCode, resultCode, data);
            return;
        }
        String token = activeToken;
        String kind = activeKind;
        activeToken = "";
        activeKind = "file";
        if (!validToken(token)) {
            Log.w(TAG, "Ignoring picker result without a valid request token");
            return;
        }
        if (resultCode != Activity.RESULT_OK || data == null || data.getData() == null) {
            writeResult(token, "cancelled", "");
            return;
        }
        Uri uri = data.getData();
        int flags = data.getFlags()
                & (Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        try {
            getContentResolver().takePersistableUriPermission(uri, flags);
        } catch (RuntimeException ignored) {
            // Some document providers grant only a task-scoped read permission.
        }
        try {
            File localCopy = copySelection(uri, token, kind);
            writeResult(token, "ok", localCopy.getAbsolutePath());
        } catch (IOException | RuntimeException exc) {
            Log.e(TAG, "Could not import selected document", exc);
            writeResult(token, "error", "无法读取所选文件");
        }
    }

    private boolean handlePickerIntent(Intent intent) {
        if (intent == null || intent.getData() == null) {
            return false;
        }
        Uri uri = intent.getData();
        if (!SCHEME.equals(uri.getScheme()) || !"open".equals(uri.getHost())) {
            return false;
        }
        String token = uri.getQueryParameter("token");
        String kind = uri.getQueryParameter("kind");
        if (!validToken(token)) {
            return true;
        }
        activeToken = token;
        activeKind = normalizeKind(kind);
        cleanupStaleFiles();

        Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        picker.addCategory(Intent.CATEGORY_OPENABLE);
        picker.addFlags(
                Intent.FLAG_GRANT_READ_URI_PERMISSION
                        | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
        );
        if ("image".equals(activeKind)) {
            picker.setType("image/*");
            picker.putExtra(
                    Intent.EXTRA_MIME_TYPES,
                    new String[]{"image/png", "image/jpeg", "image/webp"}
            );
        } else if ("json".equals(activeKind)) {
            picker.setType("application/json");
        } else {
            picker.setType("*/*");
        }
        try {
            startActivityForResult(picker, PICKER_REQUEST_CODE);
        } catch (RuntimeException exc) {
            Log.e(TAG, "Could not launch document picker", exc);
            writeResult(activeToken, "error", "系统文件选择器不可用");
            activeToken = "";
            activeKind = "file";
        }
        return true;
    }

    private File copySelection(Uri uri, String token, String kind) throws IOException {
        File root = pickerRoot();
        File imports = new File(root, "imports");
        if (!imports.isDirectory() && !imports.mkdirs()) {
            throw new IOException("Could not create picker import directory");
        }
        String suffix = safeSuffix(displayName(uri), kind);
        File partial = new File(imports, token + suffix + ".part");
        File target = new File(imports, token + suffix);
        ContentResolver resolver = getContentResolver();
        long limit = "json".equals(kind) ? JSON_LIMIT : IMAGE_LIMIT;
        long total = 0L;
        try (
                InputStream source = resolver.openInputStream(uri);
                FileOutputStream destination = new FileOutputStream(partial)
        ) {
            if (source == null) {
                throw new IOException("Document provider returned no stream");
            }
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = source.read(buffer)) != -1) {
                total += count;
                if (total > limit) {
                    throw new IOException("Selected document exceeds size limit");
                }
                destination.write(buffer, 0, count);
            }
            destination.getFD().sync();
        } catch (IOException exc) {
            partial.delete();
            throw exc;
        }
        if (total <= 0L) {
            partial.delete();
            throw new IOException("Selected document is empty");
        }
        target.delete();
        if (!partial.renameTo(target)) {
            partial.delete();
            throw new IOException("Could not finalize selected document");
        }
        return target;
    }

    private String displayName(Uri uri) {
        try (Cursor cursor = getContentResolver().query(
                uri,
                new String[]{OpenableColumns.DISPLAY_NAME},
                null,
                null,
                null
        )) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) {
                    return cursor.getString(index);
                }
            }
        } catch (RuntimeException ignored) {
        }
        return "";
    }

    private static String safeSuffix(String name, String kind) {
        String value = name == null ? "" : name.toLowerCase(Locale.ROOT);
        for (String suffix : new String[]{".png", ".jpg", ".jpeg", ".webp", ".json"}) {
            if (value.endsWith(suffix)) {
                return suffix;
            }
        }
        return "json".equals(kind) ? ".json" : ".img";
    }

    private void writeResult(String token, String status, String value) {
        if (!validToken(token)) {
            return;
        }
        File root = pickerRoot();
        if (!root.isDirectory() && !root.mkdirs()) {
            Log.e(TAG, "Could not create picker result directory");
            return;
        }
        File partial = new File(root, token + ".result.part");
        File target = new File(root, token + ".result");
        try (
                OutputStreamWriter writer = new OutputStreamWriter(
                        new FileOutputStream(partial), StandardCharsets.UTF_8
                )
        ) {
            writer.write(status);
            writer.write('\n');
            writer.write(value == null ? "" : value.replace("\n", " "));
            writer.write('\n');
        } catch (IOException exc) {
            Log.e(TAG, "Could not write picker result", exc);
            partial.delete();
            return;
        }
        target.delete();
        if (!partial.renameTo(target)) {
            Log.e(TAG, "Could not publish picker result");
            partial.delete();
        }
    }

    private File pickerRoot() {
        return new File(getFilesDir(), "banverse-picker");
    }

    private void cleanupStaleFiles() {
        File root = pickerRoot();
        File[] files = root.listFiles();
        if (files == null) {
            return;
        }
        long cutoff = System.currentTimeMillis() - STALE_AGE_MS;
        for (File file : files) {
            deleteIfStale(file, cutoff);
        }
    }

    private void deleteIfStale(File file, long cutoff) {
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) {
                for (File child : children) {
                    deleteIfStale(child, cutoff);
                }
            }
        }
        if (file.lastModified() < cutoff) {
            file.delete();
        }
    }

    private static boolean validToken(String token) {
        return token != null && token.matches("[0-9a-f]{32}");
    }

    private static String normalizeKind(String kind) {
        if ("image".equals(kind) || "json".equals(kind)) {
            return kind;
        }
        return "file";
    }
}
