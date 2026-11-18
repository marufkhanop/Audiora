"""
Seed the library with demo content.

Usage:  python manage.py seed_demo
Create an administrator separately with `python manage.py createsuperuser`.
"""
import io
import math
import struct
import wave

from PIL import Image, ImageDraw, ImageFont
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from audiobooks.models import Audiobook, Category

CATEGORIES = [
    ("Fiction", "Novels and short stories from every era."),
    ("Adventure", "Sea voyages, treasure hunts and far journeys."),
    ("Science Fiction", "Time machines, strange worlds and future odds and ends."),
    ("Mystery", "Detectives, locked rooms and quiet clues."),
    ("Philosophy", "Stoic mornings and practical wisdom."),
]

BOOKS = [
    ("Moby-Dick", "Herman Melville", "Amara Okafor", "Adventure",
     "A sailor signs aboard the Pequod and follows Captain Ahab into open water, "
     "where one white whale turns a whaling voyage into an obsession."),
    ("Pride and Prejudice", "Jane Austen", "James Whitfield", "Fiction",
     "Elizabeth Bennet trades opinions with Mr. Darcy across a series of visits, "
     "letters and misunderstandings in the English countryside."),
    ("The Time Machine", "H. G. Wells", "Priya Raman", "Science Fiction",
     "A dinner-party inventor demonstrates a small brass machine and returns with "
     "an account of the far future, told calmly over cold meat."),
    ("The Adventures of Sherlock Holmes", "Arthur Conan Doyle", "James Whitfield", "Mystery",
     "Twelve cases from the Baker Street notebooks, each solved by observation "
     "before the client finishes explaining the problem."),
    ("Meditations", "Marcus Aurelius", "Elena Marsh", "Philosophy",
     "The private notebooks of a Roman emperor: short, plain entries on duty, "
     "attention and doing the next right thing."),
    ("The Art of War", "Sun Tzu", "Daniel Kim", "Philosophy",
     "Thirteen short chapters on positioning, timing and winning without "
     "fighting, addressed to generals and useful to everyone else."),
    ("Treasure Island", "Robert Louis Stevenson", "Amara Okafor", "Adventure",
     "Jim Hawkins keeps the inn, keeps the map and keeps his nerve as the "
     "crew of the Hispaniola shows its true colours."),
    ("Frankenstein", "Mary Shelley", "Elena Marsh", "Fiction",
     "A student of natural philosophy assembles a living man and then refuses "
     "to answer for him. The creature tells his own account."),
]

COVER_BG = [
    (22, 25, 30), (27, 30, 37), (20, 23, 28), (30, 34, 41),
    (18, 21, 26), (24, 27, 33), (21, 24, 30), (26, 29, 36),
]
AMBER = (229, 169, 60)
SLATE = (148, 163, 184)
OFFWHITE = (241, 245, 249)


def make_wav(seconds: int) -> bytes:
    """Generate a quiet, low hum as placeholder narration audio."""
    rate = 8000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(rate * seconds):
            t = i / rate
            fade = min(1.0, i / (rate * 0.5), max(0.0, (rate * seconds - i) / (rate * 0.5)))
            amp = 4200 * fade * (0.75 + 0.25 * math.sin(2 * math.pi * 0.22 * t))
            value = amp * math.sin(2 * math.pi * 108 * t)
            frames += struct.pack("<h", int(value))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _load_font(size: int):
    """Find a usable TrueType font across Linux, macOS and Windows."""
    import glob

    candidates = [
        *glob.glob("/usr/share/fonts/**/DejaVuSans-Bold.ttf", recursive=True),   # Linux
        *glob.glob("/usr/share/fonts/**/DejaVuSans.ttf", recursive=True),
        *glob.glob("/usr/share/fonts/**/*.ttf", recursive=True),
        "/System/Library/Fonts/Helvetica.ttc",                                   # macOS
        "arial.ttf",                                                             # Windows
        "Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_cover(title: str, author: str, idx: int) -> bytes:
    """Typographic cover plate: flat obsidian surface, amber rule, quiet text."""
    W = H = 600
    img = Image.new("RGB", (W, H), COVER_BG[idx % len(COVER_BG)])
    d = ImageDraw.Draw(img)

    d.rectangle([48, 48, 52, 68], fill=AMBER)                     # amber tick mark
    d.rectangle([48, 92, W - 48, 94], fill=(42, 47, 55))          # hairline rule

    f_title = _load_font(40)
    f_author = _load_font(24)

    y, line_h = 124, 52
    words, lines = title.split(), []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if d.textlength(trial, font=f_title) <= W - 96:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    for line in lines[:5]:
        d.text((48, y), line, font=f_title, fill=OFFWHITE)
        y += line_h

    d.text((48, H - 84), author, font=f_author, fill=SLATE)
    d.text((48, H - 56), "Audiora Audiobooks", font=f_author, fill=(94, 102, 115))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Command(BaseCommand):
    help = "Seed the library with demo audiobooks"

    def handle(self, *args, **options):
        if Audiobook.objects.exists():
            self.stdout.write("Library already seeded, skipping demo content")
            return

        categories = {}
        for name, desc in CATEGORIES:
            cat, _ = Category.objects.get_or_create(name=name, defaults={"description": desc})
            categories[name] = cat

        for idx, (title, author, narrator, cat_name, description) in enumerate(BOOKS):
            wav = make_wav(40 + idx * 9)
            # Compute duration from the in-memory bytes — on Android FUSE
            # storage, freshly written files may not be immediately readable.
            from mutagen import File as MutagenFile
            try:
                duration = int(MutagenFile(io.BytesIO(wav)).info.length)
            except Exception:
                duration = 0
            book = Audiobook(
                title=title,
                author=author,
                narrator=narrator,
                description=description,
                category=categories[cat_name],
                published=True,
                duration=duration,
            )
            book.audio_file.save(
                f"{slugify(title)}.wav",
                ContentFile(wav),
                save=False,
            )
            book.cover.save(
                f"{slugify(title)}.png",
                ContentFile(make_cover(title, author, idx)),
                save=False,
            )
            book.save()
            self.stdout.write(f"Seeded: {title} ({book.duration}s, {book.formatted_duration})")

        self.stdout.write(self.style.SUCCESS("Demo library seeded"))
