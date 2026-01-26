# synthDrivers/extensionPoints.py
# Copyright (C) 2022 Yukio Nozawa, ACT Laboratory
# Extension point system for UML speech processing

"""
Extension Points Module

This module provides a clean hook-based system for extending UML's speech
processing functionality without monkey-patching NVDA's internal functions.

Usage:
    from . import extensionPoints

    # Register a callback for speech sequence modification
    def my_modifier(sequence, instance):
        # Modify and return the sequence
        return modified_sequence

    extensionPoints.speechSequenceModifier.register(my_modifier)

    # Later, unregister when done
    extensionPoints.speechSequenceModifier.unregister(my_modifier)
"""

from typing import Callable, Any, List, Optional
import threading


class ExtensionPoint:
    """
    A generic extension point that allows registering and notifying callbacks.

    This provides a clean alternative to monkey-patching by allowing
    multiple handlers to be registered for specific extension points.
    """

    def __init__(self, name: str = ""):
        self._name = name
        self._handlers: List[Callable] = []
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    def register(self, handler: Callable) -> None:
        """Register a callback handler for this extension point."""
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def unregister(self, handler: Callable) -> None:
        """Unregister a callback handler from this extension point."""
        with self._lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    def unregisterAll(self) -> None:
        """Unregister all handlers from this extension point."""
        with self._lock:
            self._handlers.clear()

    @property
    def handlers(self) -> List[Callable]:
        """Return a copy of the current handlers list."""
        with self._lock:
            return self._handlers.copy()

    @property
    def hasHandlers(self) -> bool:
        """Check if any handlers are registered."""
        with self._lock:
            return len(self._handlers) > 0


class FilterExtensionPoint(ExtensionPoint):
    """
    An extension point that filters/transforms data through registered handlers.

    Each handler receives the data and returns a modified version.
    Handlers are called in registration order, with each receiving
    the output of the previous handler.
    """

    def apply(self, data: Any, *args, **kwargs) -> Any:
        """
        Apply all registered handlers to the data in sequence.

        Args:
            data: The data to be filtered/transformed
            *args, **kwargs: Additional arguments passed to each handler

        Returns:
            The transformed data after all handlers have been applied
        """
        result = data
        for handler in self.handlers:
            try:
                result = handler(result, *args, **kwargs)
            except Exception:
                # Log error but continue with other handlers
                pass
        return result


class ActionExtensionPoint(ExtensionPoint):
    """
    An extension point that notifies all registered handlers of an action.

    Unlike FilterExtensionPoint, this doesn't transform data - it just
    notifies handlers that something happened.
    """

    def notify(self, *args, **kwargs) -> None:
        """
        Notify all registered handlers of an action.

        Args:
            *args, **kwargs: Arguments passed to each handler
        """
        for handler in self.handlers:
            try:
                handler(*args, **kwargs)
            except Exception:
                # Log error but continue with other handlers
                pass


class DeciderExtensionPoint(ExtensionPoint):
    """
    An extension point that allows handlers to make decisions.

    Returns True if any handler returns True, False otherwise.
    Useful for permission checks or conditional processing.
    """

    def decide(self, *args, **kwargs) -> bool:
        """
        Query all handlers for a decision.

        Args:
            *args, **kwargs: Arguments passed to each handler

        Returns:
            True if any handler returns True, False otherwise
        """
        for handler in self.handlers:
            try:
                if handler(*args, **kwargs):
                    return True
            except Exception:
                pass
        return False


# Pre-defined extension points for UML speech processing

# Called to modify speech sequences before language detection
# Handler signature: (sequence: List, instance: SynthDriver) -> List
preSpeechSequenceModifier = FilterExtensionPoint("preSpeechSequenceModifier")

# Called to modify speech sequences after language detection and splitting
# Handler signature: (sequence: List, instance: SynthDriver) -> List
postSpeechSequenceModifier = FilterExtensionPoint("postSpeechSequenceModifier")

# Called when speech is about to be spoken (for logging, analytics, etc.)
# Handler signature: (sequence: List, symbolLevel, priority, instance: SynthDriver) -> None
speechStarted = ActionExtensionPoint("speechStarted")

# Called when speech has finished
# Handler signature: (instance: SynthDriver) -> None
speechFinished = ActionExtensionPoint("speechFinished")

# Called to determine language for a character
# Handler signature: (char: int, lastKind: str) -> Optional[str]
# Return None to use default behavior, or a language code to override
languageDetector = FilterExtensionPoint("languageDetector")

# Called when language switches during speech
# Handler signature: (oldLang: str, newLang: str, instance: SynthDriver) -> None
languageSwitched = ActionExtensionPoint("languageSwitched")


def resetAll() -> None:
    """Reset all extension points by unregistering all handlers."""
    preSpeechSequenceModifier.unregisterAll()
    postSpeechSequenceModifier.unregisterAll()
    speechStarted.unregisterAll()
    speechFinished.unregisterAll()
    languageDetector.unregisterAll()
    languageSwitched.unregisterAll()
