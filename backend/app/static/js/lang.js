/**
 * lang.js — English / Mandarin language switcher
 *
 * All UI text lives here. To add a new string:
 *   1. Add it under both "en" and "zh" below.
 *   2. Put data-i18n="your_key" on the HTML element.
 *   3. Call applyLang() and it fills in automatically.
 */

const LANG = {
  en: {
    app_name:        "Keongco Sales",
    // Nav
    nav_orders:      "Orders",
    nav_products:    "Products",
    nav_customers:   "Customers",
    nav_admin:       "Admin",
    nav_logout:      "Logout",
    // Login
    login_title:     "Sign In",
    login_username:  "Username",
    login_password:  "Password",
    login_btn:       "Sign In",
    // Orders list
    orders_title:    "My Orders",
    order_new:       "New Order",
    order_status_draft:          "Draft",
    order_status_submitted:      "Submitted",
    order_status_approved:       "Approved",
    order_status_pushed_to_sage: "In Sage",
    order_status_rejected:       "Rejected",
    order_no_orders: "No orders yet.",
    // New order
    new_order_title:   "New Order",
    search_customer:   "Search customer…",
    select_customer:   "Select Customer",
    add_product:       "Add Product",
    search_product:    "Search product…",
    qty_label:         "Qty",
    price_label:       "Unit Price (RM)",
    total_label:       "Total",
    submit_order:      "Submit for Review",
    save_draft:        "Save Draft",
    // Order detail
    order_detail:      "Order Detail",
    order_id:          "Order #",
    customer_label:    "Customer",
    salesperson_label: "Salesperson",
    status_label:      "Status",
    reject_note_label: "Rejection Note",
    // Admin
    admin_pending:     "Pending Orders",
    admin_all:         "All Orders",
    approve_btn:       "Approve",
    reject_btn:        "Reject",
    push_sage_btn:     "Push to Sage",
    reject_note_ph:    "Reason for rejection…",
    confirm_approve:   "Approve this order?",
    confirm_push:      "Push to Sage 300? This cannot be undone.",
    // Products
    products_title:    "Products",
    on_hold_badge:     "On Hold",
    view_photos:       "View Photos",
    no_photos:         "No photos yet.",
    // Allocations
    alloc_remaining:   "Remaining",
    alloc_limit:       "Limit",
    // General
    loading:           "Loading…",
    error_generic:     "Something went wrong. Please try again.",
    btn_back:          "Back",
    btn_cancel:        "Cancel",
    btn_confirm:       "Confirm",
    rm_prefix:         "RM",
  },

  zh: {
    app_name:        "健高销售系统",
    // Nav
    nav_orders:      "订单",
    nav_products:    "产品",
    nav_customers:   "客户",
    nav_admin:       "管理",
    nav_logout:      "退出",
    // Login
    login_title:     "登录",
    login_username:  "用户名",
    login_password:  "密码",
    login_btn:       "登录",
    // Orders list
    orders_title:    "我的订单",
    order_new:       "新订单",
    order_status_draft:          "草稿",
    order_status_submitted:      "已提交",
    order_status_approved:       "已批准",
    order_status_pushed_to_sage: "已入账",
    order_status_rejected:       "已拒绝",
    order_no_orders: "暂无订单",
    // New order
    new_order_title:   "新建订单",
    search_customer:   "搜索客户…",
    select_customer:   "选择客户",
    add_product:       "添加产品",
    search_product:    "搜索产品…",
    qty_label:         "数量",
    price_label:       "单价 (RM)",
    total_label:       "小计",
    submit_order:      "提交审核",
    save_draft:        "保存草稿",
    // Order detail
    order_detail:      "订单详情",
    order_id:          "订单号",
    customer_label:    "客户",
    salesperson_label: "业务员",
    status_label:      "状态",
    reject_note_label: "拒绝原因",
    // Admin
    admin_pending:     "待审订单",
    admin_all:         "所有订单",
    approve_btn:       "批准",
    reject_btn:        "拒绝",
    push_sage_btn:     "推送至Sage",
    reject_note_ph:    "拒绝原因…",
    confirm_approve:   "确认批准此订单？",
    confirm_push:      "推送至 Sage 300？此操作不可撤销。",
    // Products
    products_title:    "产品列表",
    on_hold_badge:     "暂停销售",
    view_photos:       "查看照片",
    no_photos:         "暂无照片",
    // Allocations
    alloc_remaining:   "剩余配额",
    alloc_limit:       "配额上限",
    // General
    loading:           "加载中…",
    error_generic:     "出现错误，请重试。",
    btn_back:          "返回",
    btn_cancel:        "取消",
    btn_confirm:       "确认",
    rm_prefix:         "RM",
  },
};

/** Read saved language from localStorage, default to English. */
function currentLang() {
  return localStorage.getItem("lang") || "en";
}

/** Translate a key for the current language. */
function t(key) {
  return LANG[currentLang()][key] || LANG["en"][key] || key;
}

/** Fill every element that has data-i18n="key" with the translated text. */
function applyLang() {
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      el.placeholder = t(key);
    } else {
      el.textContent = t(key);
    }
  });
  // Update the toggle button label.
  const btn = document.getElementById("lang-toggle");
  if (btn) btn.textContent = currentLang() === "en" ? "中文" : "EN";
}

/** Switch language and refresh all text. */
function toggleLang() {
  localStorage.setItem("lang", currentLang() === "en" ? "zh" : "en");
  applyLang();
}

// Apply language as soon as this script loads.
document.addEventListener("DOMContentLoaded", applyLang);
