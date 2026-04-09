from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class RetryImagePreprocessor:
    def build_retry_variants(self, source_path: Path, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        variants = [
            output_dir / f"{source_path.stem}_retry_autocontrast.png",
            output_dir / f"{source_path.stem}_retry_grayscale.png",
            output_dir / f"{source_path.stem}_retry_sharpened.png",
            output_dir / f"{source_path.stem}_retry_thresholded.png",
            output_dir / f"{source_path.stem}_retry_textboost.png",
        ]
        builders = [
            self._build_autocontrast_variant,
            self._build_grayscale_variant,
            self._build_sharpened_variant,
            self._build_thresholded_variant,
            self._build_textboost_variant,
        ]
        for path, builder in zip(variants, builders):
            builder(source_path, path)
        return variants

    def build_retry_variant(self, source_path: Path, output_path: Path) -> Path:
        return self._build_autocontrast_variant(source_path, output_path)

    def _build_autocontrast_variant(self, source_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as source:
            image = source.convert("RGB")
            image = ImageOps.autocontrast(image, cutoff=1)
            image = ImageEnhance.Contrast(image).enhance(1.2)
            image = image.filter(ImageFilter.MedianFilter(size=3))
            image = image.filter(ImageFilter.UnsharpMask(radius=1.6, percent=140, threshold=2))
            image.save(output_path)
        return output_path

    def _build_grayscale_variant(self, source_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as source:
            image = ImageOps.grayscale(source)
            image = ImageOps.autocontrast(image, cutoff=1)
            image = ImageEnhance.Contrast(image).enhance(1.35)
            image = image.filter(ImageFilter.MedianFilter(size=3))
            image = image.filter(ImageFilter.UnsharpMask(radius=1.8, percent=165, threshold=2))
            image.convert("RGB").save(output_path)
        return output_path

    def _build_sharpened_variant(self, source_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as source:
            image = source.convert("RGB")
            image = ImageOps.autocontrast(image, cutoff=2)
            image = ImageEnhance.Contrast(image).enhance(1.25)
            image = ImageEnhance.Sharpness(image).enhance(1.6)
            image = image.filter(ImageFilter.UnsharpMask(radius=2.0, percent=175, threshold=2))
            image.save(output_path)
        return output_path

    def _build_thresholded_variant(self, source_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as source:
            image = ImageOps.grayscale(source)
            image = ImageOps.autocontrast(image, cutoff=1)
            image = ImageEnhance.Contrast(image).enhance(1.5)
            image = image.filter(ImageFilter.MedianFilter(size=3))
            image = image.point(lambda value: 255 if value > 168 else 0)
            image.convert("RGB").save(output_path)
        return output_path

    def _build_textboost_variant(self, source_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as source:
            image = source.convert("RGB")
            image = ImageOps.autocontrast(image, cutoff=1)
            image = ImageEnhance.Brightness(image).enhance(1.03)
            image = ImageEnhance.Contrast(image).enhance(1.4)
            image = ImageEnhance.Sharpness(image).enhance(1.9)
            image = image.filter(ImageFilter.MedianFilter(size=3))
            image = image.filter(ImageFilter.UnsharpMask(radius=1.4, percent=185, threshold=1))
            image.save(output_path)
        return output_path
