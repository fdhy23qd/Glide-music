#!/bin/sh
# Glide Music на Linux как обычное приложение:
#   sh install-linux.sh              — собрать (PyInstaller, папкой) и установить в ~/.local
#   sh install-linux.sh --uninstall  — удалить (настройки и библиотека остаются)
# Ставит: ~/.local/lib/glide-music (программа; прежняя версия — glide-music.prev), ~/.local/bin/glide-music,
# значок в теме (hicolor), ~/.local/share/applications/glide-music.desktop — появляется в лаунчере и доке.
# Сборка папкой, а не одним файлом: одиночный exe при каждом запуске распаковывает себя (~100 МБ) во временную
# папку — запуск дольше на секунды, особенно на HDD.
set -e
src=$(cd "$(dirname "$0")" && pwd)
prefix="${PREFIX:-$HOME/.local}"
lib="$prefix/lib/glide-music"
bin="$prefix/bin/glide-music"
apps="$prefix/share/applications"
icons="$prefix/share/icons/hicolor"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$lib" "$lib.prev" "$bin" "$apps/glide-music.desktop"
    for s in 16 32 48 64 128 256; do rm -f "$icons/${s}x${s}/apps/glide-music.png"; done
    update-desktop-database -q "$apps" 2>/dev/null || true
    gtk-update-icon-cache -q -t "$icons" 2>/dev/null || true
    echo "Glide Music удалён (настройки и библиотека — на месте)"
    exit 0
fi

# ---- окружение для сборки: своё, рядом с исходниками ----
venv="$src/.venv-build"
if [ ! -x "$venv/bin/pyinstaller" ]; then
    python3 -m venv "$venv"
    "$venv/bin/pip" install -q --upgrade pip
    "$venv/bin/pip" install -q -r "$src/requirements.txt" pyinstaller
fi

# ---- сборка папкой ----
cd "$src"
"$venv/bin/pyinstaller" --noconfirm --clean --onedir --windowed --name glide-music \
    --add-data "$src/icons:icons" --collect-all pypresence \
    --distpath "$src/build/linux-dist" --workpath "$src/build/linux-work" --specpath "$src/build" \
    "$src/app.py" >/dev/null
out="$src/build/linux-dist/glide-music"
[ -x "$out/glide-music" ] || { echo "сборка не удалась" >&2; exit 1; }

# ---- установка: новая версия рядом, затем подмена (прежняя — .prev) ----
mkdir -p "$prefix/lib" "$prefix/bin" "$apps"
rm -rf "$lib.new" "$lib.prev"
cp -r "$out" "$lib.new"
[ -d "$lib" ] && mv "$lib" "$lib.prev"
mv "$lib.new" "$lib"
ln -sf "$lib/glide-music" "$bin"

# ---- значок: из icons/logo.ico — PNG нужных размеров ----
python3 -I - "$src/icons/logo.ico" "$icons" <<'EOF'
import sys
from pathlib import Path
from PIL import Image
ico, root = sys.argv[1], Path(sys.argv[2])
im = Image.open(ico).convert("RGBA")   # Pillow открывает из ICO самый крупный размер
for s in (16, 32, 48, 64, 128, 256):
    d = root / f"{s}x{s}" / "apps"
    d.mkdir(parents=True, exist_ok=True)
    im.resize((s, s), Image.LANCZOS).save(d / "glide-music.png")
EOF

# ---- ярлык: полный путь в Exec (у служб и niri в PATH нет ~/.local/bin) ----
cat > "$apps/glide-music.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Glide Music
GenericName=Музыкальный плеер
Comment=Музыка из папок: обложки, мини-плеер, медиаклавиши, остров Glide Shell
Exec=$lib/glide-music
Icon=glide-music
Terminal=false
StartupWMClass=glide-music
Categories=AudioVideo;Audio;Player;
Keywords=музыка;плеер;песни;треки;music;player;mp3;flac;
EOF
update-desktop-database -q "$apps" 2>/dev/null || true
gtk-update-icon-cache -q -t "$icons" 2>/dev/null || true
echo "Glide Music установлен: $lib"
echo "  запуск — лаунчер (Mod+D) или glide-music; удалить — sh install-linux.sh --uninstall"
