"""Domain models for the AI Service Director (observations, state, context).

This package is intentionally hardware-agnostic: nothing here talks to ATEM,
PTZ, EasyWorship, or the mixer directly. It defines the vocabulary the AI
Director reasons over (see app.ai.service_director) and the authoritative
in-memory state the application owns (see docs/ai-director.md).
"""
