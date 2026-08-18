# Contributing to Church Production Director

Thank you for interest in contributing! This guide explains how to work with the codebase.

## Architecture Principles

Before starting work, understand these non-negotiable principles:

### 1. The LLM NEVER touches ATEM

```
❌ WRONG:
  LLM → ATEM SDK → Hardware

✅ CORRECT:
  LLM → LangGraph Tool → Policy Engine → ATEM Service → C++ Bridge → SDK → Hardware
```

Every AI action must pass through:
1. Policy permission check
2. Validated tool implementation
3. State verification after execution

### 2. State Verification

Never assume a command succeeded. Always verify:

```python
await atem.set_program(2)
state = await atem.status()
assert state.program_input == 2
```

### 3. Manual Control Always Works

The system must function with:
- Ollama offline
- PostgreSQL offline
- All AI services offline
- React WebSocket disconnected

Manual ATEM control via FastAPI must continue working.

### 4. Graceful Degradation

When a service fails:
- Don't crash
- Log the failure
- Continue with reduced functionality
- Notify the UI

## Coding Standards

### Python Backend

- **Style**: Black formatter, line length 100
- **Types**: Type hints on all functions
- **Logging**: Use structured logger: `logger.info("action", key=value)`
- **Errors**: Raise custom exceptions with context
- **Tests**: Unit tests for every service, mock external dependencies

```python
async def set_program(self, input_id: int) -> AtemStateModel:
    """Set ATEM program input.
    
    Args:
        input_id: Input ID to switch to.
        
    Returns:
        Updated ATEM state.
        
    Raises:
        ValueError: If input_id is invalid.
        RuntimeError: If verification fails.
    """
    # Validate
    if not await self._valid_input(input_id):
        logger.error("Invalid input", input_id=input_id)
        raise ValueError(f"Invalid input: {input_id}")
    
    # Execute with verification
    result = await self._send_command("set_program", input_id)
    
    # Verify
    state = await self.get_state()
    if state.program_input != input_id:
        raise RuntimeError("Verification failed")
    
    logger.info("Program switched", input_id=input_id)
    return state
```

### TypeScript Frontend

- **Style**: Prettier with 2-space indent
- **Types**: Strict TypeScript, no `any`
- **React**: Hooks only, no class components
- **State**: Use custom hooks for complex state

```typescript
export function useAtem() {
  const [state, setState] = useState<AtemState | null>(null)
  const [error, setError] = useState<string | null>(null)
  
  useEffect(() => {
    refreshState()
  }, [])
  
  const refreshState = async () => {
    try {
      const data = await atemAPI.getStatus()
      setState(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    }
  }
  
  return { state, error, refreshState }
}
```

### C++ Bridge

- **Standard**: C++17
- **Style**: Google C++ style guide
- **SDK**: Only use official Blackmagic SDK headers
- **Thread Safety**: Serialize all ATEM operations
- **Logging**: Use Windows event log or file logging

## Development Workflow

### 1. Pick a Phase

Work sequentially through phases:
```
Phase 1: Repository ✅ COMPLETE
Phase 2: Mock ATEM
Phase 3: FastAPI
...
Phase 17: Vision detection
```

Don't skip phases or work on multiple phases simultaneously.

### 2. Create a Branch

```bash
git checkout -b phase-2-mock-atem
```

### 3. Develop & Test

```bash
# Backend example
cd backend
pytest -v
pytest --cov=app

# Frontend example
cd frontend
npm run lint
npm run build
```

### 4. Commit with Purpose

```bash
# Good commits
git commit -m "feat: implement mock ATEM streaming state"
git commit -m "test: add ATEM cut transition tests"
git commit -m "docs: add streaming deployment guide"

# Bad commits
git commit -m "stuff"
git commit -m "fix bugs"
```

### 5. Test Before Submitting

```bash
# Backend
pytest
pytest -v --cov=app
mypy app

# Frontend
npm run lint
npm run build

# Manual testing
.\scripts\start-all.ps1
# Test functionality
```

## Testing Requirements

### Unit Tests

Every service should have tests:

```python
@pytest.mark.asyncio
async def test_set_program(mock_atem):
    """Test program switching."""
    await mock_atem.connect()
    state = await mock_atem.set_program(2)
    assert state.program_input == 2
```

### Integration Tests

Test components together:

```python
@pytest.mark.asyncio
async def test_atem_cut_transition(mock_atem, mock_camera):
    """Test cut with camera follow."""
    # Arrange
    await mock_atem.connect()
    await mock_camera.connect()
    
    # Act
    await mock_atem.set_preview(2)
    await mock_atem.cut()
    
    # Assert
    state = await mock_atem.get_state()
    assert state.program_input == 2
```

### Hardware Tests

Real ATEM tests are marked `@pytest.mark.hardware`:

```python
@pytest.mark.hardware
@pytest.mark.asyncio
async def test_real_atem_connection():
    """Test real ATEM (requires hardware)."""
    atem = AtemService(mock=False)
    assert await atem.connect("192.168.30.20")
```

Run with: `pytest -m hardware`
Skip with: `pytest -m "not hardware"`

## Documentation

### Code Comments

Explain **why**, not what:

```python
# ❌ Bad
x = y * 2  # multiply by 2

# ✅ Good
# Apply 2x gain to camera zoom level to compensate for preset camera angle
zoom_adjusted = zoom_level * 2
```

### Docstrings

Follow Google style:

```python
def switch_camera(camera_id: int) -> bool:
    """Switch ATEM program to specified camera.
    
    Args:
        camera_id: The camera ID to switch to.
        
    Returns:
        True if successful, False otherwise.
        
    Raises:
        ValueError: If camera_id is invalid.
        ConnectionError: If ATEM not connected.
        
    Example:
        >>> atem = AtemService()
        >>> await atem.connect()
        >>> success = await atem.switch_camera(1)
    """
```

### Architecture Decisions

Document non-obvious choices in `docs/`:

```markdown
# Why the LLM Can't Touch ATEM

1. **Security**: Prevents LLM prompt injection from accessing hardware
2. **Reliability**: Hardware state is guaranteed valid via verification
3. **Auditability**: Every action flows through logged systems
4. **Safety**: Policy engine enforces operational constraints
```

## Troubleshooting

### "ATEM Not Connected"

1. Verify `.env` has correct `ATEM_IP`
2. Check network connectivity: `ping 192.168.30.20`
3. Check bridge is running: `curl http://127.0.0.1:8090/health`
4. Check Windows firewall

### "Mock ATEM Not Working"

1. Ensure `enable_mock_atem=True` in `.env`
2. Check logs: `LOG_LEVEL=DEBUG`
3. Verify `MockAtemClient` is imported: `from app.atem.mock import MockAtemClient`

### "Tests Failing"

1. Ensure virtual environment is activated: `.\backend\venv\Scripts\Activate.ps1`
2. Reinstall dependencies: `pip install -r requirements.txt`
3. Check Python version: `python --version` (need 3.11+)
4. Run single test: `pytest tests/test_atem.py::test_atem_connect -v`

## Code Review Checklist

Before submitting PR:

- [ ] Code follows style guide (Black, Prettier)
- [ ] Type hints on all functions
- [ ] Unit tests added
- [ ] Tests pass locally
- [ ] No hardcoded secrets
- [ ] LLM doesn't access hardware directly
- [ ] State is verified after changes
- [ ] Manual control still works
- [ ] Graceful error handling
- [ ] Structured logging
- [ ] Documentation updated
- [ ] Commit messages are descriptive

## Getting Help

1. **Architecture questions**: Review `docs/architecture.md`
2. **API questions**: Check `/docs` at `http://localhost:8000/docs`
3. **Code examples**: Look at test files
4. **Blackmagic SDK**: Download from https://www.blackmagicdesign.com/

## Prohibited

❌ **Never:**
- Hardcode ATEM IP or any secrets
- Put business logic in React components
- Make LLM call SDK directly
- Skip state verification
- Assume commands succeed
- Break manual control
- Commit `.env` file
- Use `any` type in TypeScript
- Skip tests

✅ **Always:**
- Verify state after commands
- Handle errors gracefully
- Test with mock first
- Document non-obvious code
- Log important events
- Think about failure modes
- Keep AI separate from hardware

---

Thank you for contributing to making worship production easier and safer! 🙏
