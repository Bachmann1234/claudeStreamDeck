"""M1 hardware smoke test: paint keys + read presses on a real Stream Deck."""
import threading
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper
from PIL import Image, ImageDraw, ImageFont


def solid_key(deck, color):
    img = PILHelper.create_image(deck, background=color)
    return PILHelper.to_native_format(deck, img)


def text_key(deck, label, bg="black", fg="white"):
    img = PILHelper.create_image(deck, background=bg)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except Exception:
        font = ImageFont.load_default()
    w, h = img.size
    tb = d.textbbox((0, 0), label, font=font)
    d.text(((w - (tb[2] - tb[0])) / 2, (h - (tb[3] - tb[1])) / 2 - tb[1]),
           label, font=font, fill=fg)
    return PILHelper.to_native_format(deck, img)


def main(duration=20):
    deck = DeviceManager().enumerate()[0]
    deck.open()
    deck.reset()
    deck.set_brightness(60)
    print(f"opened {deck.deck_type()} — {deck.key_count()} keys, brightness 60%")

    deck.set_key_image(0, solid_key(deck, (0, 90, 200)))     # blue
    deck.set_key_image(1, text_key(deck, "HI"))
    deck.set_key_image(7, solid_key(deck, (0, 160, 70)))      # green (center)
    print("painted: key0=blue, key1='HI', key7=green")

    done = threading.Event()

    def on_press(dk, key, pressed):
        print(f"  KEY {key} {'DOWN' if pressed else 'up'}", flush=True)
        if pressed:
            # light the pressed key yellow on down, clear on next
            dk.set_key_image(key, solid_key(dk, (235, 185, 0)))

    deck.set_key_callback(on_press)
    print(f"listening {duration}s — press some keys...", flush=True)
    done.wait(duration)

    deck.reset()
    deck.close()
    print("reset + closed. done.")


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
