-- phpMyAdmin SQL Dump
-- version 5.2.1deb3
-- https://www.phpmyadmin.net/
--
-- Host: localhost:3306
-- Generation Time: Aug 27, 2026 at 05:16 PM
-- Server version: 8.0.46-0ubuntu0.24.04.3
-- PHP Version: 8.3.6

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `greygigz_beta`
--

-- --------------------------------------------------------

--
-- Table structure for table `categories`
--

CREATE TABLE `categories` (
  `id` int UNSIGNED NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `url` varchar(50) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `meta_title` varchar(100) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `meta_desc` varchar(255) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `meta_keywords` varchar(200) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `image` varchar(100) COLLATE utf8mb4_general_ci DEFAULT NULL,
  `description` text COLLATE utf8mb4_general_ci,
  `user_id` int NOT NULL DEFAULT '0',
  `service_id` int NOT NULL,
  `status` enum('0','1') COLLATE utf8mb4_general_ci NOT NULL DEFAULT '0' COMMENT '0 => inactive, 1 => active',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `categories`
--

INSERT INTO `categories` (`id`, `name`, `url`, `meta_title`, `meta_desc`, `meta_keywords`, `image`, `description`, `user_id`, `service_id`, `status`, `created_at`, `updated_at`) VALUES
(1, 'Banking', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(2, 'Insurance', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(3, 'Asset/Wealth Management', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(4, 'Fintech & Payments', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(5, 'Private Equity & Venture Capital', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(6, 'Family Offices', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(7, 'Healthcare Providers', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(8, 'Pharma & Biotech', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(9, 'Medical Devices', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(10, 'Education & EdTech', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(11, 'Government & Public Sector', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(12, 'Nonprofit & Foundations', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(13, 'Real Estate (Commercial/Residential)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(14, 'Construction & Engineering', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(15, 'Architecture & Urban Planning', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(16, 'Energy (Oil & Gas)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(17, 'Renewables & Utilities', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(18, 'Mining & Materials', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(19, 'Manufacturing (Discrete & Process)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(20, 'Semiconductors & Electronics', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(21, 'Telecom', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(22, 'Technology (SaaS & IT Services)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:25', NULL),
(23, 'Media, Entertainment & Sports', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(24, 'Publishing & Education Media', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(25, 'Retail (Brick & Clicks)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(26, 'eCommerce & Marketplaces', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(27, 'FMCG/CPG', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(28, 'Food & Beverage', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(29, 'Hospitality & Tourism', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(30, 'Logistics, Shipping & Aviation', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(31, 'Automotive & Mobility', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(32, 'Aerospace & Defence', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(33, 'Agriculture & Agritech', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(34, 'Chemicals', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(35, 'Professional Services (Consulting/Legal/Accounting)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(36, 'Gaming', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 06:19:26', NULL),
(37, 'Executive Leadership', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(38, 'Strategy & Corporate Development', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(39, 'Project & Program Management', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(40, 'Operations Excellence', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(41, 'Supply Chain & Logistics', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(42, 'Quality, Health, Safety & Environment (QHSE)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(43, 'Financial Planning & Analysis (FP&A)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(44, 'Accounting & Reporting', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(45, 'Tax', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(46, 'Treasury & Working Capital', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(47, 'Audit, Risk & Controls', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(48, 'Compliance & Governance', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(49, 'Investor Relations & Capital Markets', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(50, 'Corporate Development / M&A', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:26', NULL),
(51, 'Legal (Corporate & Commercial)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(52, 'Legal (Specialised)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(53, 'Human Resources / People', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(54, 'Compensation & Benefits', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(55, 'Learning & Development', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(56, 'Product Management', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(57, 'Design & UX', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(58, 'Software Engineering', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(59, 'Cloud & DevOps', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(60, 'Data & Analytics', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(61, 'Cybersecurity', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(62, 'IT Management & Enterprise Apps', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(63, 'AI & Automation', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(64, 'Marketing (Brand & Content)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(65, 'Marketing (Growth/Performance)', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(66, 'Sales & Partnerships', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(67, 'Customer Operations', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(68, 'Real Estate & Workplace', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(69, 'Sustainability / ESG', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(70, 'Business Continuity & Crisis', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(71, 'Research & Insights', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(72, 'Technical Writing & Documentation', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(73, 'Localization & Translation', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(74, 'Grants & Fundraising', NULL, NULL, NULL, NULL, NULL, NULL, 0, 2, '1', '2026-03-24 06:19:27', NULL),
(75, 'Accounting & Controllership', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:27', NULL),
(76, 'Finance', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:27', NULL),
(77, 'Legal & Compliance', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:27', NULL),
(78, 'Strategy & Transformation', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:27', NULL),
(79, 'HR & People', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(80, 'Marketing & Sales', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(81, 'Operations & Quality', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(82, 'Supply Chain & Procurement', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(83, 'IT & Data', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(84, 'Product, Design & UX', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(85, 'Innovation & R&D', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(86, 'Sustainability & ESG', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(87, 'Engineering', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(88, 'Plant, Fleet & Assets', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(89, 'Hospitality & Tourism', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(90, 'Natural Resources & Energy', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(91, 'Life Sciences & Healthcare', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(92, 'Construction & Real Estate', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(93, 'Education, Public & Nonprofit', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(94, 'Risk & Audit', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(95, 'Customer & Support', NULL, NULL, NULL, NULL, NULL, NULL, 0, 3, '1', '2026-03-24 06:19:28', NULL),
(96, 'Accounting Finance', NULL, NULL, NULL, NULL, NULL, NULL, 11491, 4, '1', '2026-03-24 08:15:04', NULL),
(97, 'Annual Audit', NULL, NULL, NULL, NULL, NULL, NULL, 11491, 4, '1', '2026-03-24 08:15:04', NULL),
(98, 'Cashflow Mgmt', NULL, NULL, NULL, NULL, NULL, NULL, 11491, 4, '1', '2026-03-24 08:15:04', NULL),
(99, 'System Development', NULL, NULL, NULL, NULL, NULL, NULL, 11491, 4, '1', '2026-03-24 08:15:04', NULL),
(100, 'Organization & Mgmt', NULL, NULL, NULL, NULL, NULL, NULL, 11491, 4, '1', '2026-03-24 08:15:04', NULL),
(101, 'Profit Improvement', NULL, NULL, NULL, NULL, NULL, NULL, 11491, 4, '1', '2026-03-24 08:15:04', NULL),
(102, 'Capital Structuring', NULL, NULL, NULL, NULL, NULL, NULL, 11491, 4, '1', '2026-03-24 08:15:04', NULL),
(103, 'Fund Raising', NULL, NULL, NULL, NULL, NULL, NULL, 11491, 4, '1', '2026-03-24 08:15:04', NULL),
(104, 'Temp Industry', NULL, NULL, NULL, NULL, NULL, NULL, 0, 1, '1', '2026-03-24 03:06:59', NULL),
(105, 'arc', NULL, NULL, NULL, NULL, NULL, NULL, 20, 4, '0', '2026-04-15 04:08:10', NULL),
(106, 'new', NULL, NULL, NULL, NULL, NULL, NULL, 20, 4, '0', '2026-04-15 04:12:36', NULL),
(107, 'new tag', NULL, NULL, NULL, NULL, NULL, NULL, 20, 4, '0', '2026-04-15 22:18:45', NULL),
(108, 'eee', NULL, NULL, NULL, NULL, NULL, NULL, 20, 4, '0', '2026-04-15 23:34:19', NULL),
(109, 'oil pipeline', NULL, NULL, NULL, NULL, NULL, NULL, 20, 4, '0', '2026-04-17 03:48:53', NULL),
(110, 'Financial Services', NULL, NULL, NULL, NULL, NULL, NULL, 11582, 4, '0', '2026-07-23 02:35:06', NULL);

--
-- Indexes for dumped tables
--

--
-- Indexes for table `categories`
--
ALTER TABLE `categories`
  ADD PRIMARY KEY (`id`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `categories`
--
ALTER TABLE `categories`
  MODIFY `id` int UNSIGNED NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=111;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
