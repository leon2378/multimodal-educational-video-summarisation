import pytest

from lecture_core.storage import MAX_PARTS, MIN_PART_BYTES, PartPlan, UploadedPart

MiB = 1024**2


def test_a_file_is_cut_into_whole_parts_and_a_shorter_last_one() -> None:
    plan = PartPlan.for_size(40 * MiB + 5, 16 * MiB)

    assert plan.count == 3
    assert [plan.size_of(number) for number in (1, 2, 3)] == [16 * MiB, 16 * MiB, 8 * MiB + 5]


@pytest.mark.parametrize(
    ("size", "count"), [(1, 1), (16 * MiB, 1), (16 * MiB + 1, 2), (32 * MiB, 2)]
)
def test_how_many_parts(size: int, count: int) -> None:
    assert PartPlan.for_size(size, 16 * MiB).count == count


def test_parts_grow_rather_than_outnumber_what_storage_allows() -> None:
    size = 400 * 1024**3
    plan = PartPlan.for_size(size, 16 * MiB)

    assert plan.part_bytes > 16 * MiB
    assert plan.count <= MAX_PARTS
    assert sum(plan.size_of(number) for number in range(1, plan.count + 1)) == size


def test_only_the_parts_that_fit_the_plan_count() -> None:
    plan = PartPlan.for_size(2 * MIN_PART_BYTES + 10, MIN_PART_BYTES)
    uploaded = [
        UploadedPart(1, MIN_PART_BYTES, '"a"'),
        UploadedPart(2, MIN_PART_BYTES - 1, '"b"'),  # cut short
        UploadedPart(3, 10, '"c"'),
        UploadedPart(4, 10, '"d"'),  # past the end
    ]

    assert sorted(plan.matching(uploaded)) == [1, 3]
