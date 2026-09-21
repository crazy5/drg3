<template>
  <el-container class="layout">
    <el-aside width="220px" class="sidebar">
      <div class="logo">DRG 3.0 分组器</div>
      <el-menu :default-active="route.path" router>
        <el-menu-item index="/">
          <el-icon><DataLine /></el-icon>
          <span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/case/new">
          <el-icon><Edit /></el-icon>
          <span>单条录入</span>
        </el-menu-item>
        <el-menu-item index="/case/batch">
          <el-icon><Upload /></el-icon>
          <span>批量导入</span>
        </el-menu-item>
        <el-menu-item index="/rules">
          <el-icon><Document /></el-icon>
          <span>规则浏览</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import { DataLine, Edit, Upload, Document } from '@element-plus/icons-vue'

const route = useRoute()
</script>

<style>
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
.layout { min-height: 100vh; }
.sidebar { background: #001529; color: #fff; }
.sidebar .logo { padding: 20px; font-size: 16px; font-weight: bold; text-align: center; border-bottom: 1px solid #1f3a5a; }
.sidebar .el-menu { background: transparent; border: none; }
.sidebar .el-menu-item { color: #c0d0e0; }
.sidebar .el-menu-item.is-active { background: #1890ff !important; color: #fff !important; }
.el-main { background: #f5f7fa; }
</style>
