package com.feihe.util;


import com.google.common.reflect.ClassPath;

import java.io.IOException;
import java.util.HashSet;
import java.util.Set;

public class ClassScanUtil {
    private ClassScanUtil() {
    }

    public static Set<Class<?>> getClasses(String packageName) throws IOException {
        ClassLoader classLoader = Thread.currentThread().getContextClassLoader();
        ClassPath classPath = ClassPath.from(classLoader);
        Set<Class<?>> classes = new HashSet<>();
        for (ClassPath.ClassInfo classInfo : classPath.getTopLevelClassesRecursive(packageName)) {
            classes.add(classInfo.load());
        }
        return classes;
    }
}
