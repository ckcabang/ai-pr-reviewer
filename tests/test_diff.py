"""Direct unit tests for the diff parser (a deliberate exception to seam-only testing)."""
from reviewer.diff import parse_diff

MODIFIED = """\
diff --git a/app/pricing.py b/app/pricing.py
index 1111111..2222222 100644
--- a/app/pricing.py
+++ b/app/pricing.py
@@ -10,7 +10,8 @@ def total(order):
     a = 1
     b = 2
-    return a + b
+    c = 3
+    return a + b + c
     d = 4
     e = 5
     f = 6
     g = 7
@@ -40,3 +41,4 @@ def other():
     x = 1
+    y = 2
     z = 3
     w = 4
"""


def test_added_and_commentable_lines_use_new_side_numbers():
    [f] = parse_diff(MODIFIED)
    assert f.path == "app/pricing.py"
    assert f.change_type == "modified"
    # hunk 1 starts at new line 10: a=10 b=11, then two added lines 12-13, then d=14 .. g=17
    assert f.added_lines == [12, 13, 42]
    # every new-side line inside a hunk is commentable, context lines included
    assert f.commentable_lines == [10, 11, 12, 13, 14, 15, 16, 17, 41, 42, 43, 44]


def test_deleted_line_is_recorded_at_its_new_side_position():
    [f] = parse_diff(MODIFIED)
    # the deleted `return a + b` sat between new lines 11 and 12, so it maps to new line 12
    assert f.deleted_positions == [12]


def test_deletion_only_change_has_no_added_lines_but_a_position():
    diff = """\
diff --git a/app/orders.py b/app/orders.py
--- a/app/orders.py
+++ b/app/orders.py
@@ -5,6 +5,4 @@ def create():
     a = 1
-    if not check_and_store(key):
-        raise Conflict()
     b = 2
     c = 3
     d = 4
"""
    [f] = parse_diff(diff)
    assert f.added_lines == []
    assert f.deleted_positions == [6]
    assert f.commentable_lines == [5, 6, 7, 8]
