"""The AI Service Director: Claude reasons over ServiceContext, never touches hardware.

See docs/ai-director.md. This package intentionally has no imports of ATEM,
PTZ, or EasyWorship clients — it only returns a DirectorDecision, which the
policy-gated app.director.action_engine translates into real actions.
"""
