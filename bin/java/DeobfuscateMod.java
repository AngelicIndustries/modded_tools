import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;
import java.util.jar.Attributes;
import java.util.jar.JarEntry;
import java.util.jar.JarFile;
import java.util.jar.JarOutputStream;
import java.util.jar.Manifest;

import com.gtnewhorizons.retrofuturagradle.shadow.org.objectweb.asm.ClassReader;
import com.gtnewhorizons.retrofuturagradle.shadow.org.objectweb.asm.ClassWriter;
import com.gtnewhorizons.retrofuturagradle.shadow.org.objectweb.asm.commons.ClassRemapper;
import com.gtnewhorizons.retrofuturagradle.util.SimpleSrgRemapper;

/** Standalone entry point for RFG's dependency remapper; no Gradle project is needed. */
public final class DeobfuscateMod {
    private static final class CountingRemapper extends SimpleSrgRemapper {
        int renamed;

        CountingRemapper(Map<String, String> mappings) {
            super(mappings);
        }

        @Override
        public String map(String name) {
            String mapped = super.map(name);
            if (mapped != null && !mapped.equals(name)) renamed++;
            return mapped;
        }
    }

    private static void stripDigests(Attributes attributes) {
        attributes.keySet().removeIf(key -> key.toString().toUpperCase(Locale.ROOT).contains("-DIGEST"));
    }

    public static void main(String[] args) throws Exception {
        Map<String, String> mappings = new HashMap<>();
        for (String line : Files.readAllLines(Paths.get(args[2]), StandardCharsets.UTF_8)) {
            String[] pair = line.split("\t", 2);
            mappings.put(pair[0], pair[1]);
        }
        CountingRemapper remapper = new CountingRemapper(mappings);
        int classes = 0;
        int changed = 0;
        try (JarFile input = new JarFile(args[0], false);
                JarOutputStream output = new JarOutputStream(Files.newOutputStream(Paths.get(args[1])))) {
            for (JarEntry entry : java.util.Collections.list(input.entries())) {
                String name = entry.getName();
                String upper = name.toUpperCase(Locale.ROOT);
                // Bytecode changes invalidate signatures. Preserve the other manifest attributes.
                if (upper.startsWith("META-INF/") && (upper.endsWith(".SF") || upper.endsWith(".RSA")
                        || upper.endsWith(".DSA") || upper.endsWith(".EC") || upper.startsWith("META-INF/SIG-"))) {
                    continue;
                }
                JarEntry copied = new JarEntry(name);
                if (entry.getTime() >= 0) copied.setTime(entry.getTime());
                output.putNextEntry(copied);
                try (InputStream data = input.getInputStream(entry)) {
                    if (upper.equals("META-INF/MANIFEST.MF")) {
                        Manifest manifest = new Manifest(data);
                        stripDigests(manifest.getMainAttributes());
                        Iterator<Attributes> sections = manifest.getEntries().values().iterator();
                        while (sections.hasNext()) {
                            Attributes attributes = sections.next();
                            stripDigests(attributes);
                            if (attributes.isEmpty()) sections.remove();
                        }
                        manifest.write(output);
                    } else if (name.endsWith(".class")) {
                        ClassReader reader = new ClassReader(data);
                        ClassWriter writer = new ClassWriter(0);
                        int before = remapper.renamed;
                        reader.accept(new ClassRemapper(writer, remapper), 0);
                        output.write(writer.toByteArray());
                        classes++;
                        if (remapper.renamed > before) changed++;
                    } else {
                        byte[] buffer = new byte[8192];
                        int count;
                        while ((count = data.read(buffer)) != -1) output.write(buffer, 0, count);
                    }
                }
                output.closeEntry();
            }
        }
        System.out.printf("Processed %d classes; remapped %d names in %d classes.%n", classes, remapper.renamed, changed);
    }
}
