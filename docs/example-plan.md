# Example plan

## Tasks

### Task T1: greet by name

- [ ] **Step 1: Write the failing test**

Create `tests/test_greet.py`:

```python
from greet import greet


def test_greet():
    assert greet("Ada") == "Hello, Ada."
```

- [ ] **Step 2: Make it pass**

Create `greet.py`:

```python
def greet(name: str) -> str:
    return f"Hello, {name}."
```
