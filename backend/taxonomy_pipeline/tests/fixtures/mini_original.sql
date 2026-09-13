-- Minimal phpMyAdmin-style fixture exercising: an exact-duplicate category
-- pair (case-insensitive), an orphan speciality, an unused tag, and a
-- dead-end category/speciality - one of each thing `integrity` should catch.

INSERT INTO `categories` (`id`, `name`, `service_id`) VALUES
(1, 'Accounting', 1),
(2, 'accounting', 1),
(3, 'Marketing', 1);

INSERT INTO `specialities` (`id`, `name`, `category_id`) VALUES
(10, 'Tax Advisory', 1),
(11, 'Brand Strategy', 3),
(12, 'Orphan Speciality', 999);

INSERT INTO `tags` (`id`, `name`) VALUES
(100, 'tax'),
(101, 'gst'),
(102, 'unused-tag');

INSERT INTO `speciality_tags` (`id`, `speciality_id`, `tag_id`) VALUES
(1000, 10, 100),
(1001, 10, 101),
(1002, 999, 100);
